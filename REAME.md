# AIQ Platform

## Setup

**1. Clone the repo**
```bash
git clone https://github.com/ViralKariya-VK/aiq_platform.git
cd aiq_platform/aiq_project
```

**2. Create conda environment**
```bash
conda create -n aiq_env python=3.10
conda activate aiq_env
```

**3. Install dependencies**
```bash
pip install django python-dotenv requests
```

**4. Create `.env` file**

Create a file called `.env` in the `aiq_project` folder with:
```
SECRET_KEY=any-random-string-here
DEBUG=True
GROQ_API_KEY=your-groq-api-key-here
```

Get a free Groq API key at: https://console.groq.com

**5. Run migrations**
```bash
python manage.py migrate
```

**6. Load sample data**
```bash
python manage.py loaddata core/fixtures/initial_data.json
```

**7. Run the server**
```bash
python manage.py runserver
```

**8. Login at** `http://127.0.0.1:8000`

## Test Accounts

| Role    | Username  | Password  |
|---------|-----------|-----------|
|Superuser| Viral     | Viral@1301 |
| Teacher | teacher1  | viral1234  |
| Student | 86092500047  | viral1234  |