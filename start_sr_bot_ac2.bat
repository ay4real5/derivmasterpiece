@echo off
cd /d C:\Users\ayori\derivmasterpiece
.venv\Scripts\pythonw.exe -u run_sr_bot.py --env-file .env.ac2 --app-id 343GsiWjpyIskHP1nbTzi --output-prefix ac2 --symbol R_50 --minutes 1440 --poll 10 --duration 55 --stake-ladder "5,11.14,13.46,20.75,31.98,49.28,75.9,117.05,180.39,278.00,428.43" --max-daily-loss 1300 --target-profit 500 --cooldown 120 --max-per-line 50 --no-confirm --direction both --require-wick --adaptive-tolerance --retire-after-losses 2 --rescan-minutes 15 >> sr_bot_ac2.log 2>&1
