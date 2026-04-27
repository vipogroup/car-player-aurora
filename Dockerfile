# למקומי:   docker build -t unblocked . && docker run -p 5600:5600 unblocked
# ל-Render: חברי את הפרויקט מ-GitHub, Render יזהה Dockerfile

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UNBLOCKED_PLAYER_HOST=0.0.0.0
# ב-Render/Railway מגדירים PORT אוטומטית — unblocked מזהה אותו (ייתכן 10000+)

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY unblocked_player.py .
COPY car-music-icon.png* ./

EXPOSE 5600
# מקומי: docker run -p 5600:5600 -e PORT=5600   או: השאר ברירת 5600 בקוד

CMD ["python", "-u", "unblocked_player.py"]
