#!/usr/bin/env python3
"""Run one job of a GitHub Actions workflow against a local AWS emulator (MiniStack).

`run:` steps execute verbatim from the workflow file. The `uses:` actions that
cannot target an emulator are replaced by shims that make the same AWS API call
(see SHIMS). Setup-only actions are no-ops (see NOOP).

ponytail: not a general runner, only what the Jangingmall ECR pipelines use.
Expressions support context lookups, literals, == != && || ! and the status
functions; anything else raises. Use nektos/act if the workflows outgrow this.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile

import yaml

ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "")
REGISTRY = os.environ.get("EMU_REGISTRY", "localhost:4566")
NOOP = ("actions/checkout", "actions/setup-java", "actions/setup-python", "actions/cache",
        "actions/upload-artifact", "docker/setup-buildx-action")
TOKEN = re.compile(r"'(?:[^']|'')*'|&&|\|\||[=!]=|!|[(),]|[A-Za-z_][\w.\-]*|\d+")


class StepFailed(Exception):
    pass


class Needs(dict):
    """Upstream jobs are not run here; they are assumed green (logged at start)."""

    def get(self, key, default=None):
        return {"result": "success", "outputs": {}}


def evaluate(expr, ctx, failed=False):
    if TOKEN.sub("", expr).strip():
        raise ValueError(f"unsupported expression: {expr!r}")

    def get(path):
        cur = ctx
        for part in path.split("."):
            cur = cur.get(part, "") if isinstance(cur, dict) else ""
        return cur

    funcs = {"always": lambda: True, "success": lambda: not failed, "failure": lambda: failed}
    words = {"&&": " and ", "||": " or ", "!": " not ", "true": "True", "false": "False", "null": "''"}
    py = []
    for tok in TOKEN.findall(expr):
        if tok[0] == "'":
            py.append(repr(tok[1:-1].replace("''", "'")))
        elif tok in words:
            py.append(words[tok])
        elif tok in ("==", "!=", "(", ")", ",") or tok in funcs or tok.isdigit():
            py.append(tok)
        else:
            py.append(f"get({tok!r})")
    return eval("".join(py), {"__builtins__": {}}, {"get": get, **funcs})


def interp(value, ctx):
    """Expand ${{ }} in a YAML scalar; non-strings become their GitHub string form."""
    if not isinstance(value, str):
        return json.dumps(value)

    def sub(m):
        v = evaluate(m.group(1), ctx)
        return json.dumps(v) if isinstance(v, bool) else str(v)

    return re.sub(r"\$\{\{(.*?)\}\}", sub, value)


def aws(args, env):
    r = subprocess.run(["aws", *args, "--output", "json"], env=env, text=True, capture_output=True)
    if r.returncode:
        raise StepFailed(r.stderr.strip())
    return json.loads(r.stdout)


def configure_aws_credentials(w, env, ctx, opts):
    """Real action: GitHub OIDC token -> sts:AssumeRoleWithWebIdentity. Same call, unsigned token."""
    role = w.get("role-to-assume", "")
    if not role:
        raise StepFailed("role-to-assume is empty (real action: 'Credentials could not be loaded')")
    claims = {"iss": "https://token.actions.githubusercontent.com", "aud": "sts.amazonaws.com",
              "sub": f"repo:{ctx['github']['repository']}:ref:{ctx['github']['ref']}"}
    jwt = ".".join(base64.urlsafe_b64encode(json.dumps(p).encode()).decode().rstrip("=")
                   for p in ({"alg": "none", "typ": "JWT"}, claims)) + ".emulator"
    out = aws(["sts", "assume-role-with-web-identity", "--role-arn", role,
               "--role-session-name", "GitHubActions", "--web-identity-token", jwt], env)
    creds, region = out["Credentials"], w.get("aws-region", env.get("AWS_REGION", ""))
    print(f"assumed {out['AssumedRoleUser']['Arn']}")
    return {}, {"AWS_ACCESS_KEY_ID": creds["AccessKeyId"], "AWS_SECRET_ACCESS_KEY": creds["SecretAccessKey"],
                "AWS_SESSION_TOKEN": creds["SessionToken"], "AWS_REGION": region, "AWS_DEFAULT_REGION": region}


def amazon_ecr_login(w, env, ctx, opts):
    """Real action logs docker in to proxyEndpoint (*.dkr.ecr.*.amazonaws.com) and outputs that host.
    The emulator serves the registry protocol on its own port, so that is the one deviation."""
    auth = aws(["ecr", "get-authorization-token"], env)["authorizationData"][0]
    user, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
    r = subprocess.run(["docker", "login", "--username", user, "--password-stdin", REGISTRY],
                       input=password, text=True, capture_output=True)
    if r.returncode:
        raise StepFailed(r.stderr.strip())
    print(f"proxyEndpoint {auth['proxyEndpoint']} -> logged in to {REGISTRY}")
    return {"registry": REGISTRY}, {}


def build_push(w, env, ctx, opts):
    """docker/build-push-action without the GHA cache backend (cache-from/to are ignored)."""
    meta = tempfile.mktemp(suffix=".json")
    cmd = ["docker", "buildx", "build", "--metadata-file", meta]
    for tag in w.get("tags", "").split():
        cmd += ["--tag", tag]
    if w.get("platforms"):
        cmd += ["--platform", w["platforms"]]
    if w.get("push") == "true":
        cmd.append("--push")
    stdin = None
    if opts.stub_build:
        # Images too heavy to build locally (multi-GB ML images): same tags, same push, stub content.
        stdin = f"FROM busybox:1.36\nLABEL emulator.stub-for={w.get('context', '.')} revision={ctx['github']['sha']}\n"
        cmd.append("-")
    else:
        for arg in w.get("build-args", "").split():
            cmd += ["--build-arg", arg]
        if w.get("file"):
            cmd += ["--file", w["file"]]
        if w.get("target"):
            cmd += ["--target", w["target"]]
        cmd.append(w.get("context", "."))
    if subprocess.run(cmd, cwd=opts.workdir, env=env, input=stdin, text=True).returncode:
        raise StepFailed("docker build/push failed")
    with open(meta) as f:
        return {"digest": json.load(f).get("containerimage.digest", "")}, {}


SHIMS = {"aws-actions/configure-aws-credentials": configure_aws_credentials,
         "aws-actions/amazon-ecr-login": amazon_ecr_login,
         "docker/build-push-action": build_push}


def run_step(script, env, cwd, shell, image, opts):
    fd, path = tempfile.mkstemp(suffix=".sh")
    os.write(fd, script.encode())
    os.close(fd)
    os.chmod(path, 0o644)  # mkstemp is 0600; the in-image runner reads it as another uid
    # GitHub: explicit `shell: bash` adds pipefail, the default shell does not.
    argv = ["bash", "--noprofile", "--norc", "-eo", "pipefail"] if shell == "bash" else ["bash", "-e"]
    if image:
        # Stand-in for a setup-* action: run the step in an image that has the toolchain.
        # No env is forwarded, so this is only for steps that need none.
        rel = os.path.relpath(cwd, opts.workdir)
        return subprocess.call(["docker", "run", "--rm", "-v", f"{opts.workdir}:/w", "-w", f"/w/{rel}",
                                "-v", "emu-gradle-cache:/root/.gradle", "-v", f"{path}:/step.sh:ro",
                                image, *argv, "/step.sh"])
    return subprocess.call(argv + [path], cwd=cwd, env=env)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workflow")
    ap.add_argument("job")
    ap.add_argument("--workdir", required=True, help="checked-out repo the job runs in")
    ap.add_argument("--sha", required=True)
    ap.add_argument("--event", default="push")
    ap.add_argument("--ref", default="refs/heads/main")
    ap.add_argument("--repo", default="Jangingmall/unknown")
    ap.add_argument("--var", action="append", default=[], metavar="K=V", help="Actions variable (vars.K)")
    ap.add_argument("--input", action="append", default=[], metavar="K=V", help="workflow_dispatch input")
    ap.add_argument("--matrix", action="append", default=[], metavar="K=V", help="select a matrix.include leg")
    ap.add_argument("--skip", action="append", default=[], metavar="REGEX", help="skip steps by name")
    ap.add_argument("--in-image", action="append", default=[], metavar="REGEX=IMAGE",
                    help="run matching steps inside this container image")
    ap.add_argument("--stub-build", action="store_true", help="push a stub image instead of building the context")
    ap.add_argument("--out", help="write step results as JSON")
    opts = ap.parse_args()
    opts.workdir = os.path.abspath(opts.workdir)

    if not re.fullmatch(r"http://(localhost|127\.0\.0\.1):\d+", ENDPOINT):
        sys.exit("refusing to run: AWS_ENDPOINT_URL must point at the local emulator")

    def pairs(items):
        return dict(i.split("=", 1) for i in items)

    with open(opts.workflow) as f:
        wf = yaml.safe_load(f)
    job = wf["jobs"][opts.job]
    inputs, select = pairs(opts.input), pairs(opts.matrix)
    legs = job.get("strategy", {}).get("matrix", {}).get("include", [{}])
    matrix = next((leg for leg in legs if all(str(leg.get(k)) == v for k, v in select.items())), None)
    if matrix is None:
        sys.exit(f"no matrix leg matches {select}")

    ctx = {"github": {"sha": opts.sha, "ref": opts.ref, "event_name": opts.event, "repository": opts.repo,
                      "server_url": "https://github.com", "run_id": "1", "run_attempt": "1",
                      "event": {"inputs": inputs}},
           "vars": pairs(opts.var), "inputs": inputs, "secrets": {}, "matrix": matrix,
           "needs": Needs(), "steps": {}, "env": {}}
    for scope in (wf, job):
        for k, v in (scope.get("env") or {}).items():
            ctx["env"][k] = interp(v, ctx)

    print(f"== {opts.workflow} :: {opts.job}  matrix={matrix or '-'}  (needs.* assumed success)", flush=True)
    if "if" in job and not evaluate(job["if"].strip("${} "), ctx):
        sys.exit(f"job condition is false: {job['if']}")

    tmp = tempfile.mkdtemp(prefix="emu-run-")
    summary = os.path.join(tmp, "summary.md")
    open(summary, "w").close()
    base_env = {**os.environ, "CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_SHA": opts.sha,
                "GITHUB_REF": opts.ref, "GITHUB_EVENT_NAME": opts.event, "GITHUB_REPOSITORY": opts.repo,
                "GITHUB_WORKSPACE": opts.workdir, "GITHUB_STEP_SUMMARY": summary, "RUNNER_TEMP": tmp,
                "RUNNER_NAME": "ministack-local", "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "1"}
    exported = {}  # what a step would append to $GITHUB_ENV
    defaults = {**(wf.get("defaults") or {}).get("run", {}), **(job.get("defaults") or {}).get("run", {})}
    in_image = [i.split("=", 1) for i in opts.in_image]
    failed, rows = False, []

    for i, step in enumerate(job["steps"]):
        name = interp(step.get("name") or step.get("uses") or step["run"].splitlines()[0], ctx)
        sid = step.get("id", f"_{i}")
        cond = str(step.get("if", "success()")).strip("${} ")
        if not re.search(r"\b(always|success|failure)\(", cond):
            cond = f"success() && ({cond})"
        outputs, outcome, kind = {}, "skipped", "if=false"
        if any(re.search(p, name) for p in opts.skip):
            kind = "--skip"
        elif evaluate(cond, ctx, failed):
            print(f"\n--- {name}", flush=True)
            env = {**base_env, **ctx["env"], **exported}
            try:
                if "run" in step:
                    step_env = {k: interp(v, ctx) for k, v in (step.get("env") or {}).items()}
                    env.update(step_env)
                    env["GITHUB_OUTPUT"] = os.path.join(tmp, f"output-{i}")
                    open(env["GITHUB_OUTPUT"], "w").close()
                    script = interp(step["run"], {**ctx, "env": {**ctx["env"], **step_env}})
                    cwd = os.path.join(opts.workdir, interp(step.get("working-directory")
                                                            or defaults.get("working-directory", "."), ctx))
                    image = next((img for pat, img in in_image if re.search(pat, name)), None)
                    kind = f"run in {image}" if image else "run"
                    if run_step(script, env, cwd, step.get("shell") or defaults.get("shell"), image, opts):
                        raise StepFailed("non-zero exit")
                    with open(env["GITHUB_OUTPUT"]) as f:
                        outputs = dict(line.rstrip("\n").split("=", 1) for line in f if "=" in line)
                else:
                    action = step["uses"].split("@")[0]
                    if action in SHIMS:
                        kind = "shim"
                        with_ = {k: interp(v, ctx) for k, v in (step.get("with") or {}).items()}
                        outputs, env_out = SHIMS[action](with_, env, ctx, opts)
                        exported.update(env_out)
                    elif action in NOOP:
                        kind = "no-op"
                    else:
                        raise StepFailed(f"no shim for action {action}")
                outcome = "success"
            except StepFailed as e:
                print(f"::error:: {e}", flush=True)
                outcome, failed = "failure", True
        ctx["steps"][sid] = {"outputs": outputs, "outcome": outcome, "name": name}
        rows.append((outcome, kind, name))

    print("\n== steps")
    for outcome, kind, name in rows:
        print(f"  [{outcome:7}] {kind:10} {name}")
    status = "failure" if failed else "success"
    print(f"== job {status}", flush=True)
    if opts.out:
        with open(summary) as f, open(opts.out, "w") as out:
            json.dump({"status": status, "steps": ctx["steps"], "summary": f.read()}, out, indent=1, ensure_ascii=False)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
