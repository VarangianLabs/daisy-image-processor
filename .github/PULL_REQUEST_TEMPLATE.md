## Summary

<!-- One sentence: what does this PR do? -->

## Related issue

Closes #

---

## Checklist

- [ ] `make test` passes locally (all tests green)
- [ ] `make lint` produces no errors
- [ ] If `src/requirements.txt` changed — `make vendor && make build` was run and `vendor/` is **not** committed
- [ ] If a new environment variable was added — `.env.example` is updated with a comment
- [ ] Commit messages are descriptive (e.g. `fix: guard against zero-byte SQS body`)
- [ ] No `terraform.tfstate`, `.env`, or `terraform/lambda.zip` files are included in this PR
