# נגן מוזיקה לרכב

Repository: https://github.com/vipogroup/car-

## קישורים למשתמשים

- נגן ציבורי: https://vipogroup.github.io/car-/car-player-standalone.html
- דף הוראות מרכזי: https://vipogroup.github.io/car-/portal.html

## אופציה נוספת: שרת מקומי מהמחשב למובייל

אם המשתמש רוצה לעבוד דרך השרת המקומי שלו:

1. הורדה: https://raw.githubusercontent.com/vipogroup/car-/main/local-server-unblocked.zip
2. חליצה במחשב
3. התקנת Python 3.10+
4. הרצת: `pip install -r requirements.txt`
5. הרצת: `start-server-lan.bat`
6. במובייל (אותה Wi-Fi): `http://<IP-של-המחשב>:5600`
7. בדיקה: `http://<IP-של-המחשב>:5600/__player_check`

מדריך מלא: `LOCAL-SERVER-SETUP.md`

## הערה

השרת המקומי עובד רק כשהמחשב של המשתמש דולק ומריץ את הנגן.
