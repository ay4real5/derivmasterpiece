@echo off
cd /d C:\Users\ayori\derivmasterpiece
.venv\Scripts\pythonw.exe -u run_sr_bot.py --env-file .env.ac2 --app-id 343GsiWjpyIskHP1nbTzi --output-prefix ac2 --symbol R_50 --minutes 1440 --poll 10 --stake 5 --duration 55 --martingale-steps 7 --martingale-mult 2 --max-daily-loss 700 --target-profit 500 --cooldown 120 --max-per-line 50 --no-confirm --direction both --require-wick --adaptive-tolerance --retire-after-losses 2 --rescan-minutes 15 >> sr_bot_ac2.log 2>&1
