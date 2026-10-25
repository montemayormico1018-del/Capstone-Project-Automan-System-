# AutoMan Car Care System

An automotive service management web application designed to streamline car care operations, including customer management, appointments, job orders, inventory, sales, and service quotations.

## About the Project

**AutoMan Car Care** is a capstone project developed to help automotive service centers manage their daily operations through a centralized web-based system. It provides dedicated features for customers, administrators, and staff to improve workflow organization and service management.

## Features

- **User Authentication** — User login, registration, and session management.
- **Customer Management** — Manage customer information and records.
- **Appointment Management** — Handle service bookings and appointment schedules.
- **Job Order Management** — Organize vehicle service jobs and track job orders.
- **Inventory Management** — Monitor inventory items and stock levels.
- **Sales Management** — Record and manage sales transactions.
- **Service Packages** — Manage available service packages and offerings.
- **Quotation Management** — Process service quotations and customer approvals.
- **Loyalty Management** — Support customer loyalty features and rewards.
- **Reports and Analytics** — Review operational data and business performance.
- **Notifications** — Support system notifications and configured messaging features.
- **Audit Logs** — Track relevant system activities.

## Technology Stack

- **Backend:** Python, Flask
- **Frontend:** HTML, CSS, JavaScript
- **Database:** Firebase Cloud Firestore
- **Authentication:** Firebase Authentication / configured authentication utilities
- **Deployment Configuration:** Firebase and Google Cloud configuration files

## Project Structure

```text
automan-system-revisions/
├── app.py
├── config.py
├── requirements.txt
├── Procfile
├── firebase.json
├── firebase_config.py
├── firestore_db.py
├── models/
├── routes/
├── scripts/
├── static/
│   ├── css/
│   ├── icons/
│   ├── images/
│   └── js/
├── templates/
├── tests/
├── utils/
└── README.md
```

*Note: The test files are located in the project root, rather than in a dedicated `tests/` directory.*

## Prerequisites

Before running the project, install:

- Python compatible with the project's `.python-version` configuration
- pip
- Git
- A Firebase project configured for the application's required services

## Installation and Setup

### 1. Clone the repository

```bash
git clone https://github.com/montemayormico1018-del/automan-system-revisions.git
cd automan-system-revisions
```

If you rename the repository on GitHub, update the repository URL and folder name accordingly.

### 2. Create a virtual environment

**Windows:**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root and configure the environment variables required by the application.

Use `.env.example` as a reference if it documents the required variables. Add your own Firebase project settings and other required configuration values.

**Security:** Never commit `.env`, Firebase service-account credentials, private keys, or other secrets to GitHub.

### 5. Configure Firebase

Set up the Firebase project and configure the required authentication and Firestore access. Follow the project's deployment documentation for the applicable configuration.

Do not upload private service-account credentials to the repository.

### 6. Run the application

Use the startup command documented for the project. If `app.py` supports direct Flask execution, you can run:

```bash
python app.py
```

Open the local URL printed in the terminal, commonly `http://127.0.0.1:5000`.

## Running Tests

The repository includes automated test files for different application features.

Run the test suite using:

```bash
python -m pytest
```

## Documentation

Additional project documentation is available in the repository:

- `DATABASE_DESIGN.md` — Database design documentation.
- `IMPLEMENTATION_GUIDE.md` — Implementation guidance.
- `IMPLEMENTATION_PLAN.md` — Implementation planning.
- `QUICK_REFERENCE.md` — Quick reference information.
- `DEPLOY.md` — Deployment instructions.
- `FIREBASE_MIGRATION.md` — Firebase migration documentation.

Refer to these files for project-specific details and configuration instructions.

## Security and Privacy

- Keep environment variables and credentials private.
- Never commit Firebase service-account keys or private API credentials.
- Apply appropriate Firebase Authentication and Firestore security rules.
- Do not use production credentials when running tests unless explicitly required and safely configured.

## Project Status

Developed as a Bachelor of Science in Information Technology capstone project.

## License

No license has been specified yet. All rights remain with the respective project owner unless a license is added to this repository.
