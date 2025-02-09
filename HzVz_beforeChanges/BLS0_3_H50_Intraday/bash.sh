cd "/root/liveAlgos/HzVz/BLS0_3_H50_Intraday"
/usr/local/bin/pm2 start "strategyLauncher.py" --interpreter="/root/liveAlgos/venv/bin/python3" --name="BLS0_3_H50_Intraday-1" --no-autorestart --time