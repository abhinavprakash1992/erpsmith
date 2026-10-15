# ERPSmith

**AI dev copilot for ERPNext.** Build and debug DocTypes, scripts and workflows from Claude over the Model Context Protocol, behind a plan gate, with semantic memory. Frappe v15 and v16. Free and open source (MIT).

Not affiliated with or endorsed by Frappe Technologies. ERPNext and Frappe are trademarks of Frappe Technologies Pvt. Ltd.

## Development

```bash
cd dev && docker compose up -d
docker compose exec -T bench15 bash -lc "bash /workspace/erpsmith/dev/setup-bench.sh version-15 python3.12 test15.localhost 1 22"
docker compose exec -T bench16 bash -lc "bash /workspace/erpsmith/dev/setup-bench.sh version-16 python3.14 test16.localhost 2 24"
dev/test.sh
```
