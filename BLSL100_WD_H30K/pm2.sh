cd "/root/liveAlgos/HzVz/BLSL100_WD_H30K"
/usr/local/bin/pm2 start "strategyLauncher.py" --interpreter="/root/liveAlgos/venv/bin/python3" --name="BLSL100_WD_H30K-1" --no-autorestart --time