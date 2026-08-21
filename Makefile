install:
	python -m pip install -r requirements.txt
backend:
	uvicorn backend.main:app --reload --port 8000
frontend:
	streamlit run frontend/app.py
test:
	pytest -q
