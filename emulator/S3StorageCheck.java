import com.jangingmall.backend.image.application.ImageStorage;
import com.jangingmall.backend.image.application.ImageStorageProperties;
import com.jangingmall.backend.image.domain.ImagePurpose;
import com.jangingmall.backend.image.infrastructure.ImageStorageConfig;
import com.jangingmall.backend.image.infrastructure.S3ImageStorage;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import software.amazon.awssdk.services.s3.S3Client;

/**
 * Runs the backend's real S3 code (ImageStorageConfig + S3ImageStorage, wired by Spring exactly as in
 * the app) against the emulator. The repo's own S3ImageStorageTest mocks S3Client/S3Presigner, so the
 * wire behaviour (presigned URL, HeadObject metadata, 404 handling) is otherwise never exercised.
 *
 * Run by emulator/test.sh with the classpath of the built bootJar layers; no source file is added to backend/.
 */
public class S3StorageCheck {

    static final String ENDPOINT = System.getenv("AWS_ENDPOINT_URL_S3");
    static final String WEBP = "image/webp";
    static final long MAX = 10L * 1024 * 1024;
    static int failed;

    static void check(String name, boolean ok) {
        System.out.println((ok ? "  ✅ " : "  ❌ ") + name);
        if (!ok) {
            failed++;
        }
    }

    /** Uploads the way the frontend does: plain HTTP PUT to the presigned URL. */
    static int put(String url, String contentType, byte[] body) throws Exception {
        if (!url.startsWith(ENDPOINT + "/")) {
            throw new IllegalStateException("presigned URL does not point at the emulator, refusing to send: "
                + URI.create(url).getHost());
        }
        HttpRequest request = HttpRequest.newBuilder(URI.create(url)).header("Content-Type", contentType)
            .PUT(HttpRequest.BodyPublishers.ofByteArray(body)).build();
        return HttpClient.newHttpClient().send(request, HttpResponse.BodyHandlers.discarding()).statusCode();
    }

    public static void main(String[] args) throws Exception {
        ImageStorageProperties properties = new ImageStorageProperties();
        properties.setBucket(System.getenv("IMAGE_UPLOAD_BUCKET"));
        properties.setReturnBucket(System.getenv("IMAGE_RETURN_BUCKET"));
        String bucket = properties.getBucket();
        String returnBucket = properties.getReturnBucket();
        byte[] image = new byte[4096];
        String run = "9001/emu" + System.nanoTime(); // <memberId>/<imageId>, same layout as ImageService.objectKey

        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {
            context.registerBean(ImageStorageProperties.class, () -> properties);
            context.register(ImageStorageConfig.class, S3ImageStorage.class);
            context.refresh();
            ImageStorage storage = context.getBean(ImageStorage.class);
            S3Client s3 = context.getBean(S3Client.class);

            String key = "images/product/" + run + "/1280w.webp";
            String url = storage.presignPut(ImagePurpose.PRODUCT, key, WEBP, image.length, Duration.ofMinutes(5));
            check("presignPut: 코드 수정 없이 AWS_ENDPOINT_URL_S3 만으로 에뮬레이터의 공개 버킷을 가리킴",
                url.startsWith(ENDPOINT + "/" + bucket + "/" + key));
            check("presigned URL 로 PUT (FE 업로드 방식) -> 200", put(url, WEBP, image) == 200);
            check("isValid: 형식·크기 정책 통과", storage.isValid(ImagePurpose.PRODUCT, key, WEBP, MAX));
            check("isValid: 최대 크기 초과 -> false", !storage.isValid(ImagePurpose.PRODUCT, key, WEBP, image.length - 1));
            check("isValid: Content-Type 불일치 -> false", !storage.isValid(ImagePurpose.PRODUCT, key, "image/png", MAX));
            check("isValid: 없는 객체 -> false (HeadObject 404 처리)",
                !storage.isValid(ImagePurpose.PRODUCT, key + ".missing", WEBP, MAX));
            check("exists: 있는 객체 true, 없는 객체 false",
                storage.exists(ImagePurpose.PRODUCT, key) && !storage.exists(ImagePurpose.PRODUCT, key + ".missing"));

            String contentKey = "images/content/" + run + "/hero.webp";
            storage.put(ImagePurpose.CONTENT, contentKey, WEBP, image);
            check("put: 서버 측 업로드 후 크기·형식 일치", storage.isValid(ImagePurpose.CONTENT, contentKey, WEBP, MAX)
                && s3.headObject(b -> b.bucket(bucket).key(contentKey)).contentLength() == image.length);
            storage.delete(ImagePurpose.CONTENT, contentKey);
            check("delete 후 exists false", !storage.exists(ImagePurpose.CONTENT, contentKey));

            String returnKey = "images/return/" + run + "/1280w.webp";
            String returnUrl = storage.presignPut(ImagePurpose.RETURN, returnKey, WEBP, image.length, Duration.ofMinutes(5));
            check("RETURN 은 반품 버킷으로 서명", returnUrl.startsWith(ENDPOINT + "/" + returnBucket + "/" + returnKey));
            check("반품 버킷 presigned PUT -> 200", put(returnUrl, WEBP, image) == 200);
            check("반품 객체가 버킷 기본 암호화(aws:kms)로 저장됨", "aws:kms".equals(
                s3.headObject(b -> b.bucket(returnBucket).key(returnKey)).serverSideEncryptionAsString()));
            check("반품 객체는 공개 버킷에 없음 (버킷 분리)", !storage.exists(ImagePurpose.PRODUCT, returnKey));

            String strictUrl = storage.presignPut(ImagePurpose.PRODUCT, "images/product/" + run + "/640w.webp", WEBP,
                image.length, Duration.ofMinutes(5));
            check("서명과 다른 Content-Type 으로 PUT -> 403", put(strictUrl, "image/png", image) == 403);
            check("서명을 변조한 URL 로 PUT -> 403", put(
                strictUrl.replaceAll("X-Amz-Signature=[0-9a-f]{8}", "X-Amz-Signature=00000000"), WEBP, image) == 403);
        }
        System.exit(failed == 0 ? 0 : 1);
    }
}
