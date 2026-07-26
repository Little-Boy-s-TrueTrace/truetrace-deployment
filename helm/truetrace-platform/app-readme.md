# TrueTrace Multi-Agent Compliance Platform

Chart này triển khai TrueTrace cho Kubernetes:

- Spring Boot core API và PostgreSQL;
- Kafka/Redis cho event và state;
- ba agent Deepfake Inspector, Money-Trail Explorer và AML Reporter;
- cổng khách hàng và compliance command center;
- Nginx gateway và health/resource policies.

Mặc định agent chạy `demo` mode để cài đặt local không cần cloud credential. Với
production, cấu hình Alibaba Model Studio/eKYC endpoint, DashScope API key,
identity registry gateway và secret nội bộ qua Kubernetes Secret hoặc external
secret manager. STR luôn được tạo dưới dạng nháp để chuyên viên AML duyệt.

Yêu cầu: Kubernetes 1.25+ và Helm 3.
