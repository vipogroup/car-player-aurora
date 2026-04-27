# נגן מוזיקה לרכב — מערכת Unblocked (מומלץ) 🎶🚗

Repository: [github.com/vipogroup/car-](https://github.com/vipogroup/car-)

## מערכת חדשה: `unblocked_player.py` (Python + `yt-dlp`)

הנגן המלא (ספרייה, אהובים, פלייליסטים, YouTube) רץ כשרת HTTP מקומי על **פורט 5600**.

### דרישות
- Python 3.10+ מומלץ
- `pip install -r requirements.txt` (למשל `yt-dlp`)

### הפעלה
1. פתחי טרמינל בתיקיית הפרויקט.
2. אחת האפשרויות:
   - **`start-server.bat`** — משחררת את פורט 5600 אם תפוס, מפעילה את השרת ואופציונית פותחת דפדפן
   - **`restart-player.ps1`** (PowerShell) — ריסטארט בטוח, בלי `taskkill` ידני
   - **`start-unblocked-player.bat`** — השרת בלבד (ללא שינוי `OPEN_BROWSER` חיצוני)
   - `python -u unblocked_player.py`
3. בדפדפן: **http://127.0.0.1:5600/**  
4. בדיקה שהשרת הנכון רץ: **http://127.0.0.1:5600/__player_check** — אמור להחזיר `OK_UNBLOCKED_PLAYER_V5`

אם הדף נראה ישן, נסי **רענון קשיח** (`Ctrl+F5`).

### חבילה `car-music-player.zip`
מכילה את קבצי הפרויקט לעבודה ללא `git` (יוצר בבריאת הגרסה; נשמר גם ב-repo).

---

## מצבים נוספים (אופציונלי)

### נגן סטטי (ללא `unblocked`)
- `index.html` / `python -m http.server` — **מוגבל** (CORS/YouTube).
- `car-player-standalone.html` — גרסת עמית עצמאית, מתאים ל-`manifest.json` (PWA).

### PWA
- `manifest.json` + `car-music-icon.png` + `service-worker.js`
- `start_url` מצביע ל־`car-player-standalone.html` (מצב legacy).

## קבצים עיקריים
| קובץ | תפקיד |
|------|--------|
| `unblocked_player.py` | שרת + ממשק ראשי |
| `requirements.txt` | תלויות Python |
| `start-server.bat` / `restart-player.ps1` | הפעלה/ריסטארט |
| `car-player-standalone.html` | נגן PWA/סטאנדאלון (legacy) |
| `index.html` | נגן סטטי ישן (אופציוני) |
| `README.md` | המסמך הזה |

---

## כפתורי שליטה (נגן Unblocked)
הממשק כולל ניגון, רשימה, אהובים, פלייליסטים, מצב רכב, חיפוש YouTube (בהתאם לגירסה). פרטים בממשק.

## רישיון
שימוש פנימי/פרויקט — לפי מדיניות הארגון.
