@echo off
setlocal
set "LLM_BASE_URL=http://127.0.0.1:8311/v1"
set "LLM_API_KEY=sk-local"
set "LLM_MODEL=qwen3-8b"
cd /d "C:\Repo\hydraclaim"
echo === HydraClaim live demo ===
bash scripts\demo.sh
echo.
echo === Re-ingest all scenarios for benchmark ===
for %%f in (data\sessions\*.json) do python -m hydraclaim.ingest "%%f"
echo.
echo === Benchmark ===
python -m hydraclaim.benchmark data\sessions\*.json --arm all
echo.
echo done > demo\record-done.txt
echo Recording complete. Closing in 3 seconds...
timeout /t 3 /nobreak >nul
