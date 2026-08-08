@echo off
cd /d C:\Users\ayori\derivmasterpiece
.venv\Scripts\pythonw.exe -u run_sr_bot.py --env-file .env.ac2 --app-id 343GsiWjpyIskHP1nbTzi --output-prefix ac2 --symbol R_50 --minutes 1440 --poll 10 --stake 5 --duration 55 --martingale-steps 6 --martingale-mult 2 --max-daily-loss 400 --target-profit 500 --cooldown 120 --max-per-line 50 --no-confirm --direction call >> sr_bot_ac2.log 2>&1
