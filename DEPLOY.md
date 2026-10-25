# Deploying AutoMan to Google Cloud Run

AutoMan runs on Cloud Run because your database (Firestore) and logins (Firebase
Auth) are already Google services in project `automan-car-care`, billing is
already enabled (Blaze plan), and deployment uploads this folder directly — no
Git or GitHub account needed.

Files already prepared in this project:
- `wsgi.py` — production entry point (`gunicorn` runs this)
- `Procfile` — tells Cloud Run how to start the server
- `requirements.txt` — includes `gunicorn`
- `.gcloudignore` — keeps `firebase-credentials.json` and `.env` OFF the server
  (Cloud Run's own service account replaces the credentials file)
- `app.py` — `SECRET_KEY` and `BASE_URL` now come from environment variables

## Part A — One-time setup (~15 minutes)

1. Install the Google Cloud CLI:
   https://cloud.google.com/sdk/docs/install (Windows 64-bit installer).
   Accept the defaults; let it run `gcloud init` at the end or skip it.

2. Open a NEW PowerShell window and log in with the SAME Google account that
   owns your Firebase project:

       gcloud auth login

3. Point it at your project and enable the needed services:

       gcloud config set project automan-car-care
       gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com

## Part B — Deploy

4. Generate a strong secret key (copy the output):

       python -c "import secrets; print(secrets.token_urlsafe(48))"

5. From inside the project folder (`automan system`), deploy:

       gcloud run deploy automan --source . --region asia-southeast1 --allow-unauthenticated --set-env-vars "FIREBASE_PROJECT_ID=automan-car-care,SECRET_KEY=PASTE_KEY_FROM_STEP_4,TZ=Asia/Manila"

   - `asia-southeast1` is Singapore — closest region to the Philippines.
   - First build takes ~5–10 minutes. At the end it prints your public URL,
     e.g. `https://automan-xxxxxxxxxx-as.a.run.app`.

6. Set the remaining environment variables. Copy the values from your local
   `.env` file (FIREBASE_API_KEY, EMAIL_ADDRESS, EMAIL_PASSWORD) and use the
   URL from step 5 as BASE_URL — this is what makes SMS quotation links point
   at the live site:

       gcloud run services update automan --region asia-southeast1 --set-env-vars "TZ=Asia/Manila,BASE_URL=https://YOUR-URL-FROM-STEP-5,FIREBASE_API_KEY=...,EMAIL_ADDRESS=...,EMAIL_PASSWORD=..."

## Part C — After deploy (do not skip)

7. **Change the demo passwords.** The site is now public and the seeded demo
   accounts (admin@automan.com / Admin123! etc.) are guessable. In Firebase
   Console → Authentication → Users, reset the passwords for admin, staff,
   and mechanic (or do it from the Users page in the app).

8. **Tuqlas chatbot**: in your Tuqlas dashboard, add the run.app URL to the
   chatbot's allowed websites — otherwise it refuses to answer on the live
   site (same issue we saw with 127.0.0.1).

9. **Firebase authorized domains**: Firebase Console → Authentication →
   Settings → Authorized domains → add your run.app domain.

10. Test on the live URL: landing page, login, customer booking, quotation
    SMS link (should now start with your run.app URL), mechanic checklist,
    and installing the PWA from a phone.

## Updating the app later

After changing code, just run the step-5 deploy command again. Environment
variables are remembered between deploys.

## Costs

Cloud Run has a permanent free tier (2M requests/month) — a system this size
normally stays at ₱0/month. Firestore usage is billed on your existing Blaze
plan exactly as it is today.

## If Firestore permissions fail on the live site

The service uses Cloud Run's default service account instead of your
`firebase-credentials.json`. If you ever see permission errors in the logs,
grant it access once:

    gcloud projects add-iam-policy-binding automan-car-care --member="serviceAccount:$(gcloud projects describe automan-car-care --format='value(projectNumber)')-compute@developer.gserviceaccount.com" --role="roles/firebase.admin"
