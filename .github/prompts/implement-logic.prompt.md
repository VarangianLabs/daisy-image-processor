---
description: Generates clean, decoupled Python logic matching the system architecture
tools: [file_search, read_file]
---

# System Implementation Instruction

You are generating core application components for our Python-based serverless media processing pipeline. 

## Mandatory Context Checklist
Before writing code, inspect the following workspace primitives:
1. `daisy.md` for architectural boundaries.
2. `terraform/main.tf` to ensure variable alignments match resource names.

## Generation Execution Rules
* **Strict Decoupling:** Keep core business logic independent of AWS data schemas. 
* **Memory Management:** Ensure streams or explicit context managers are utilized when processing file binaries to prevent memory fragmentation.
* **Error Vectors:** Wrap external S3 actions in explicit try/except blocks and handle transient failures natively.

Generate the code block for the file requested by the user below. Include comprehensive logging and clean docstrings.