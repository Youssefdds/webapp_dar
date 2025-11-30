# Gutenberg Library – Setup Guide

## 1. Create and activate the virtual environment
```bash
python3 -m venv env
source env/bin/activate
```
## 2. Download books from Gutendex
```bash
python3 download_gutendex.py
mv libraryBooks daar_library/
```
## 3. Start Elasticsearch and Kibana
```bash
docker compose up -d elasticsearch1
docker compose up -d kibana1
```
## 4. Go to the api directory and Create the Elasticsearch index
```bash
cd daar_library
curl -X PUT "http://localhost:9200/books?pretty"
```
## 5. Run Django commands and start the Django backend 
```bash
python manage.py migrate
python manage.py import_books_withImage
python manage.py index_inverted_from_db

python manage.py runserver

```
## 6. Run API performance tests with Locust
```bash
 locust -f locustfile.py --host http://localhost:8000

```
## 7. Start the React frontend
```bash
cd ../library-frontend
npm install
npm start
```
