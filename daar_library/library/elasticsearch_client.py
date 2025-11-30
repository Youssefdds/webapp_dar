from elasticsearch import Elasticsearch

es = Elasticsearch(
    hosts=["http://elasticsearch1:9200"],
    request_timeout=60,   # augmente le timeout
    retry_on_timeout=True,
    max_retries=10,
)
INDEX_NAME = "books"

mapping = {
    "mappings": {
        "properties": {
            "id": {"type": "integer"},
            "title": {"type": "text"},
            "author": {"type": "text"},
            "text_content": {"type": "text"},  # pour le match
            "image_url": {"type": "keyword"},
        }
    }
}
