"""Public errors contain fixed messages, never provider bodies or credentials."""


class ServiceError(Exception):
    status_code = 500
    code = "internal_error"
    message = "Không thể xử lý yêu cầu."
    # Opaque gateway call id (never a body/credential), returned for audit correlation.
    request_id: str | None = None

    def __init__(self):
        super().__init__(self.message)


class ProviderError(ServiceError):
    status_code = 502
    code = "provider_error"
    message = "Dịch vụ AI không khả dụng hoặc trả dữ liệu không hợp lệ."


class GuardrailBlocked(ProviderError):
    status_code = 400
    code = "guardrail_blocked"
    message = "Yêu cầu bị guardrail chặn vì vi phạm chính sách an toàn."


class BudgetExceeded(ProviderError):
    status_code = 429
    code = "llm_budget_exceeded"
    message = "Đã vượt ngân sách LLM của workload này."


class InvalidDocument(ServiceError):
    status_code = 422
    code = "invalid_document"
    message = "Tài liệu trống, không hợp lệ hoặc không có nội dung văn bản."


class DocumentNotFound(ServiceError):
    status_code = 404
    code = "document_not_found"
    message = "Không tìm thấy tài liệu."


class DocumentConflict(ServiceError):
    status_code = 409
    code = "document_conflict"
    message = "Document ID đã gắn với nội dung hoặc cấu hình xử lý khác."


class IndexIdentityConflict(ServiceError):
    status_code = 409
    code = "index_identity_conflict"
    message = (
        "Embedding identity không khớp index. Cần rebuild index bằng cấu hình đã chọn."
    )


class SchemaMismatch(ServiceError):
    status_code = 503
    code = "schema_mismatch"
    message = "Schema chưa sẵn sàng hoặc dimension không khớp EMBEDDING_DIM."
