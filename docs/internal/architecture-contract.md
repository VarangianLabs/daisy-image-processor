# Daisy System Architecture & AI Orchestration Document

## 1. Project Overview
* **Project Name:** Serverless Image Processing Service[cite: 1]
* **Architectural Tier:** Beginner (Executed with Legendary Discipline)[cite: 1]
* **Target Objective:** Build a cost-effective, event-driven serverless pipeline that ingests raw user images via S3 pre-signed URLs, decouples execution via an SQS queue buffer to prevent downstream stress, applies transformations (resizing/watermarking), and stores them in a distribution bucket[cite: 1].

---

## 2. Technical Stack & Tooling
* **Infrastructure as Code (IaC):** Terraform (Declarative, modular architecture)
* **Local Emulation Platform:** LocalStack via Docker Compose (100% offline cloud development)
* **Compute Engine:** AWS Lambda (Python 3.12 runtime, optimized for x86_64 architecture)[cite: 1]
* **Storage Engine:** AWS S3 (Separate `source` and `processed` data spaces)[cite: 1]
* **Decoupling Layer:** AWS SQS (Simple Queue Service) acting as a traffic shock absorber
* **Media Processing Core:** `Pillow` (High-performance Python imaging library)[cite: 1]
* **IDE & Automation:** VS Code + GitHub Copilot Pro

---

## 3. Strict Architectural Restrictions (Zero Exceptions)
1. **No Direct Base64 API Payload Ingestion:** Images must *never* be uploaded as strings directly through API Gateway to prevent hitting the 10MB payload ceiling. The system must use S3 Pre-signed URLs for direct client-to-bucket communication.
2. **Strict S3 Isolation Rules:** The Lambda handler must *never* write processed data back into the original input bucket[cite: 1]. This entirely eliminates the risk of an infinite invocation loop that drives up cloud costs.
3. **Clean Decoupling:** The processing logic must remain pure Python and be entirely separated from AWS event handling logic. This ensures full local testability without needing to invoke AWS execution contexts.

---

## 4. The AI Engineer Software Engineering Cycle
We follow a 5-stage iteration cycle to maximize collaboration with GitHub Copilot Pro:

1. **PLANNING & CONTEXT:** Define constraints, schemas, and API contracts inside this `base.md` before writing executable blocks.
2. **DESIGN (IaC):** Write declarative Terraform blueprints first. Validate infrastructure relationships before writing application code.
3. **IMPLEMENTATION:** Instruct Copilot to generate pure, isolated Python code utilizing `boto3` and `Pillow`, adhering strictly to the architecture described here[cite: 1].
4. **EVALUATION (Local Simulation):** Deploy the infrastructure to LocalStack. Fire simulated S3 event payloads locally using the AWS CLI. Inspect Docker logs to observe behavioral state.
5. **DEPLOYMENT:** Run automated validation tests locally before provisioning production environments.

---

## 5. System Data Flow
[Client Request] ──> (Get Pre-signed URL) ──> [API/Lambda] ──> Returns URL
[Client Upload]  ──> (Direct PUT Binary)   ──> [S3 Source Bucket][cite: 1]
[S3 Bucket Event]──> (Asynchronous Event)  ──> [AWS SQS Queue] 
[SQS Message]    ──> (Throttled Batching)  ──> [Processing Lambda]
[Processing]     ──> (Pillow Transformation)──> [S3 Processed Bucket][cite: 1]