from locust import HttpUser, between, task

class DjangoUser(HttpUser):
    wait_time = between(1, 3)

    @task(3)
    def search_basic(self):
        self.client.get("/api/search/?q=test&page=1&size=10")

    @task(3)
    def search_regex(self):
        self.client.get("/api/search/regex/?q=the&page=1&size=10")

    @task(5)
    def enhanced_search(self):
        self.client.get("/api/enhanced-search/?q=love&regex=false&centrality=true&page=1&size=10")

    @task(1)
    def suggestions(self):
        self.client.get("/api/suggestions/?id=120")

    @task(1)
    def book_content(self):
        self.client.get("/api/book_content/?id=120")
