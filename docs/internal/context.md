# Context & Domain Blueprint: Serverless Image Processor

> **AI Instruction:** Enforce the following domain taxonomy and architectural limits across all Python, Bash, and Terraform files.

## 1. Project Objective & Core Goal
An asynchronous, event-driven media ingestion pipeline designed to process user asset uploads securely and cost-effectively. The system utilizes direct-to-storage ingestion to completely eliminate transit API gateway server overhead and scales fluidly using decoupled message queues.

## 2. Ubiquitous Language (Domain Glossary)
| Term | Exact Code Identifier | Definition & Boundaries |
| :--- | :--- | :--- |
| **Source Bucket** | `source-images-bucket` | The raw S3 storage area where users upload unmodified assets. It triggers the event pipeline. |
| **Processed Bucket** | `processed-images-bucket` | The destination S3 storage area where optimized, transformed assets land. *Must never trigger notifications.* |
| **Ingestion Payload** | `s3_event_record` | The structurally strict AWS S3 event schema delivered via an SQS message packet. |
| **Media Transformer** | `image_processor_handler` | The standalone Python core module that applies modifications (Pillow) independent of AWS primitives. |

## 3. Architectural Guardrails (Hard Rules)
*   **Absolute Isolation:** The `processed-images-bucket` must never hook into an SQS trigger line. This is a non-negotiable defensive boundary to prevent recursive code execution loop costs.
*   **Fail-Fast Shells:** Every `.sh` automation skill script must begin with `set -euo pipefail` to guarantee immediate process execution termination upon encountering unhandled errors.
*   **Memory Ceiling:** Image manipulations inside the Python layer must utilize byte streams (`io.BytesIO`) to keep the memory profile under 512MB per invocation.

## 4. Primary Data Lifecycle
1. **Ingestion:** Direct browser upload to `source-images-bucket` via a secure Pre-signed URL.
2. **Notification:** S3 signs an `ObjectCreated:Put` payload and automatically routes it to the SQS queue line.
3. **Processing:** AWS Lambda consumes the queue message, passes the raw binary to the Media Transformer, and stores the optimized output inside the `processed-images-bucket`.

## 5. Non-Goals (Out of Scope)
*   We are not building a web frontend application or user login authentication screen.
*   We are not handling video or audio transformations.