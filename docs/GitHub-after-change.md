# אחרי כל שינוי או שדרוג — מה לעשות ב-GitHub (פעם אחת לכל שחרור)

מסמך זה מיועד למי שמדחף קוד ל־`vipogroup/car-` (ו־`car-player-aurora` אם מסנכרנים). המטרה: **GitHub Pages**, **זיהוי גרסה (fingerprint)**, ו־**מתקין Windows** — כולם עקביים.

## מיתוס: “העוזר מחובר ל-GitHub ומעדכן לבד את כל המשתמשים”

- **אין** חיבור קבוע של Cursor/AI לחשבון GitHub שלך ולמחשבי משתמשים.  
- **עדכון לקוד ב-GitHub** = `git commit` + `git push` (ידני או בסשן עם העוזר).  
- **משתמשים שמותקנת אצלם “המערכת”** — תלוי **איך** הם משתמשים:
  - **דפדפן / PWA מ־`github.io`** — מקבלים עדכוני UI דרך **מנגנון ה-fingerprint** (בדיקה + חלון “עדכן עכשיו”) כשהקבצים בשרת GitHub מתעדכנים.
  - **העתק מקומי אחרי `.exe` / תיקייה ב־`%LOCALAPPDATA%\CarPlayer-Aurora`** — הקבצים שם **לא** נמשכים אוטומטית מ-GitHub בכל פתיחה. כדי גרסה חדשה צריך **להריץ מתקין חדש**, או **להחליף את התיקייה** (ZIP חדש), או סקריפט עדכון שאתם מספקים.

---

## 1. דחיפה ל־Git (`main`)

```text
git add …
git commit -m "…"
git push origin main
git push github-aurora main
```

(או רימוט אחד — לפי מה שבשימוש.)

---

## 2. לחכות ל־Actions (אוטומטי בדחיפה)

| Workflow | מתי רץ | מה הוא עושה |
|----------|---------|----------------|
| **Aurora bundle fingerprint** | כל push ל־`main`/`master` (בלי `[skip ci]` ב־commit) | מעדכן `aurora/bundle-fingerprint.json` אם השתנה hash של קבצי UI. |
| **Build Windows installer** | **לא** אוטומטי על כל push | רק ידני או תג `car-installer-*`. |

אם ה־fingerprint workflow נכשל — לתקן ולדחוף שוב.

---

## 3. מתקין Windows — חובה אחרי שינוי ב־`installer/` או בקבצים שבתוך ה־`.iss`

1. ב־GitHub: **Actions → Build Windows installer → Run workflow** (ענף `main`).  
2. בסיום: **Artifacts** → הורד `CarPlayerAurora-Setup.exe` ובדוק על מחשב נקי (או VM).  
3. **אופציונלי אבל מומלץ לשימוש ציבורי:** ליצור **Release** עם הנכס בשם המדויק  
   `CarPlayerAurora-Setup.exe`  
   כדי שהקישור יעבוד:  
   `https://github.com/vipogroup/car-/releases/latest/download/CarPlayerAurora-Setup.exe`  

   דרך מהירה: לדחוף **תג** בפורמט  
   `car-installer-v1.0.1`  
   — ה־workflow יוצר Release עם הקובץ (אם `gh release create` הצליח).

---

## 4. GitHub Pages

- לוודא שבהגדרות הריפו **Pages** מצביע על הענף/תיקייה הנכונים (בדרך כלל `main` + `/ (root)`).  
- אחרי דחיפה: לפתוח  
  `https://vipogroup.github.io/car-/aurora/index.html`  
  ולבצע **רענון קשיח** (או “עדכן עכשיו” אם מופיע).

---

## 5. בדיקות עשןן (2 דקות)

- [ ] דף הבית נטען, אין שגיאות ב־Console (F12).  
- [ ] `client-version` / fingerprint — אין חלון עדכון שגוי אחרי שדרוג רגיל.  
- [ ] הורדת `CarPlayerAurora-Setup.exe` מה־Release (או Artifact) מתחילה ולא 404.  
- [ ] מודאל **התקנה למחשב חדש** — הקישור להורדה עובד או שהטקסט מפנה ל־PowerShell כשאין Release.

---

## 6. מתי **לא** צריך לבנות מתקין מחדש

שינויים **רק** ב־`aurora/*.js` / CSS / HTML — בדרך כלל מספיק דחיפה + fingerprint; **אין** חובה להריץ את בונה המתקין בכל קומיט.

---

## סיכום בשורה אחת

**דחוף `main` → ודא ש־fingerprint ירוק → הרץ ידנית (או תג) את בונה המתקין כשצריך `.exe` → ודא Pages וקישור ההורדה.**
