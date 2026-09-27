# MLOps Architecture Overview — ghi chú Day 4 (4 block)

Phạm vi: overview để DevOps phối hợp với ML team, không hands-on. InsightHub hiện gọi
model qua API (Gemini/Anthropic/OpenAI/Ollama), chưa có model tự train.

## Block 1 — ML lifecycle map

```
Data → Feature/Prep → Train → Evaluate → Register → Approve → Deploy/Serve → Monitor ─┐
  ↑                                                                                   │
  └────────────── Retrain (khi drift / chất lượng giảm) ◄────────────────────────────┘
```

- Khác vòng đời app: app đổi khi **code** đổi; model còn đổi khi **data** đổi, dù code không đổi.
- Artifact khác nhau ở 4 chiều:

| Chiều | App artifact | Model artifact |
|---|---|---|
| Nội dung | image từ source code | weights + preprocessing + config train |
| Đầu vào tái tạo | commit SHA | code + **dataset version** + hyperparameters + seed |
| Kiểm thử | unit/integration pass/fail | metric trên holdout (accuracy, F1…), có ngưỡng |
| Suy giảm | không tự hỏng khi môi trường không đổi | hỏng dần khi dữ liệu thật trôi (drift) |

## Block 2 — 4 khái niệm core

- **Model Registry**: kho version model (vd. MLflow Model Registry) kèm metadata: dataset, metric,
  người train, stage (staging/production). Là "ECR của model".
- **Approval Gate**: model chỉ lên production khi qua ngưỡng metric + review của ML owner
  (giống manual approval của pipeline apply Day 3).
- **Drift**:
  - *Data drift*: phân phối **input** thay đổi (vd. tài liệu upload đổi ngôn ngữ/định dạng).
  - *Concept drift*: quan hệ input → output đúng thay đổi (cùng câu hỏi nhưng đáp án đúng đã khác).
  - Phát hiện bằng so sánh phân phối (PSI, KS test) hoặc metric chất lượng theo thời gian.
- **Rollback**: quay về version model trước trong registry (đổi alias/stage), không retrain gấp.

## Block 3 — Ownership boundary DevOps vs ML Engineer

| Stage | DevOps / Platform | ML Engineer / Data Scientist |
|---|---|---|
| Data, feature, train, evaluate | cung cấp hạ tầng (GPU, storage, pipeline runner) | **PRIMARY** |
| Register + approval | vận hành registry, quyền truy cập | **PRIMARY** (quyết định approve) |
| Deploy / serve | **PRIMARY** (K8s, autoscale, canary, rollout/rollback cơ chế) | định nghĩa contract model |
| Monitor hạ tầng (latency, error, GPU, cost) | **PRIMARY** | tham gia |
| Monitor chất lượng / drift | cung cấp pipeline telemetry + alert routing | **PRIMARY** (diễn giải, quyết định) |
| Retrain | chạy job khi được yêu cầu | **PRIMARY** (quyết định retrain) |

## Block 4 — Khi drift fire, DevOps làm gì

1. Không tự retrain (DevOps không bao giờ tự quyết retrain: đó là quyết định về chất lượng model).
2. Xác nhận không phải lỗi hạ tầng (latency, error, dữ liệu input bị cắt/đổi format do pipeline).
3. Route alert tới ML owner kèm evidence (metric, timestamp, version model đang serve).
4. Nếu ML owner yêu cầu: rollback về version trước trong registry bằng cơ chế deploy sẵn có.
5. Liên hệ InsightHub: đổi embedding model = đổi "vector space" → phải reindex (Day 1 constraint
   `index_identity_conflict`), tương tự rollback model phải đi cùng version index.
