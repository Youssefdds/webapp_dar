from rest_framework.decorators import api_view
from rest_framework.response import Response
from library.elasticsearch_client import es, INDEX_NAME
from library.models import Book
from rest_framework.decorators import api_view
from rest_framework.response import Response
from library.elasticsearch_client import es, INDEX_NAME
from django.http import JsonResponse
from elasticsearch import NotFoundError
from django.http import StreamingHttpResponse
from django.http import JsonResponse
from django.test import Client
import networkx as nx
from networkx.algorithms import centrality
from itertools import combinations

import pickle
from pathlib import Path
import json


INDEX_NAME = "books"

BOOK_MAP = {}          # mis à jour par les endpoints de recherche
GRAPH = nx.Graph()     # graphe global
CENTRALITY = {}        # centralité calculée
SUGGESTIONS = {}       # suggestions calculées
_LAST_BOOK_MAP = {}    # pour détecter changement

GRAPH_GLOBAL = None
CENTRALITY_GLOBAL = None
SUGGESTIONS_GLOBAL = None
TOP_N = 3  # nombre de suggestions par livre
GRAPH_READY = False


GRAPH_FILE = Path("./graph_books.json") 
client = Client()

def save_graph_to_file(graph, centrality, suggestions):
    with GRAPH_FILE.open("wb") as f:
        pickle.dump({
            "graph": graph,
            "centrality": centrality,
            "suggestions": suggestions
        }, f)


@api_view(["GET"])
def search_books(request):
    global BOOK_MAP  
    query = request.GET.get("q", "").lower()
    page = int(request.GET.get("page", 1))
    size = int(request.GET.get("size", 10))
    start = (page - 1) * size

    if not query:
        return Response({"page": page, "size": size, "total": 0, "results": []})

    body = {
        "from": 0,
        "size": 1000,
        "query": {
            "regexp": {
                "term": {"value": query}
            }
        }
    }

    res = es.search(index=INDEX_NAME, body=body)
    hits = res["hits"]["hits"]

    # Récupérer tous les book_ids et occurrences
    book_map = {}  # { book_id: total_occurrences }
    for hit in hits:
        for bid, count in hit["_source"]["books"].items():
            bid_int = int(bid)
            book_map[bid_int] = book_map.get(bid_int, 0) + count

    total = len(book_map)
    if total == 0:
        return Response({"page": page, "size": size, "total": 0, "results": []})

    # Pagination côté Python
    sorted_books = sorted(book_map.items(), key=lambda x: -x[1])  # tri par occurrences
    paginated = sorted_books[start:start + size]

    # Charger les livres depuis Django
    book_ids = [bid for bid, _ in paginated]
    books = Book.objects.filter(id__in=book_ids)
    books_dict = {book.id: book for book in books}

    results = []
    for bid, occ in paginated:
        book = books_dict.get(bid)
        if book:
            results.append({
                "id": book.id,
                "title": book.title,
                "author": book.author,
                "image_url": book.image_url,
                "score": occ
            })

    return Response({"page": page, "size": size, "total": total, "results": results})
@api_view(["GET"])
def search_regex(request):
    global all_books_terms
    global BOOK_MAP  

    pattern = request.GET.get("q", "").lower()
    page = int(request.GET.get("page", 1))
    size = int(request.GET.get("size", 10))
    start = (page - 1) * size

    if not pattern:
        return Response({"page": page, "size": size, "total": 0, "results": []})

    body = {
        "query": {
            "regexp": {
                "term": {"value": pattern}
            }
        },
        "size": 10000
    }

    es_results = es.search(index=INDEX_NAME, body=body)
    hits = es_results["hits"]["hits"]

    # Extraire book_ids et occurrences
    book_map = {}
    for hit in hits:
        for bid, count in hit["_source"]["books"].items():
            bid_int = int(bid)
            book_map[bid_int] = book_map.get(bid_int, 0) + count

    
    total_books = len(book_map)
    if total_books == 0:
        return Response({"page": page, "size": size, "total": 0, "results": [] })



    # Pagination côté Python
    sorted_books = sorted(book_map.items(), key=lambda x: -x[1])
    paginated = sorted_books[start:start + size]
    
    top_books = [bid for bid, _ in sorted_books[:10]]  # les 10 livres les plus pertinents
    #suggestions, centrality = generate_suggestions(book_map, all_books_terms, top_n=3)
    #print("book_map:", book_map)
    #print("centrality:", centrality)
    # Charger les livres depuis Django

    BOOK_MAP = book_map

    book_ids = [bid for bid, _ in paginated]
    books = Book.objects.filter(id__in=book_ids)
    books_dict = {book.id: book for book in books}

    results = []
    for bid, occ in paginated:
        book = books_dict.get(bid)
        if book:
            results.append({
                "id": book.id,
                "title": book.title,
                "author": book.author,
                "image_url": book.image_url,
                "score": occ
            })

    return Response({"page": page, "size": size, "total": total_books, "results": results })
    update_graph_if_needed(all_books_terms)
    
@api_view(["GET"])
def book_content(request):
    INDEX_1="books_index"
    book_id = request.GET.get("id")
    if not book_id:
        return JsonResponse({"error": "ID parameter is required"}, status=400)

    try:
        res = es.get(index=INDEX_1, id=book_id)
        text_content = res["_source"].get("text_content", "")

        def text_generator():
            chunk_size = 1024  # 1 KB per chunk
            for i in range(0, len(text_content), chunk_size):
                yield text_content[i:i+chunk_size]

        response = StreamingHttpResponse(text_generator(), content_type="text/plain")
        response['Content-Disposition'] = f'inline; filename="{res["_source"].get("title","book")}.txt"'
        return response
    except NotFoundError:
        return JsonResponse({"error": "Book not found in Elasticsearch"}, status=404)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    



def fetch_all_terms():
    """
    Retourne un dict {book_id: set(terms)} à partir de l'index Elasticsearch.
    """
    all_books_terms = {}

    # Scroll ou size élevé si l'index n'est pas trop gros
    body = {
        "query": {"match_all": {}},
        "size": 10000  # récupère tous les termes
    }
    res = es.search(index=INDEX_NAME, body=body)
    hits = res["hits"]["hits"]

    for hit in hits:
        term = hit["_source"]["term"]
        books = hit["_source"]["books"]  # ex: {"3357": 40, ...}
        for book_id in books.keys():
            book_id = int(book_id)
            if book_id not in all_books_terms:
                all_books_terms[book_id] = set()
            all_books_terms[book_id].add(term)

    return all_books_terms
    


all_books_terms = fetch_all_terms()

def build_graph_from_books():
    print("📘 Construction du graphe depuis Django Book...")

    books = Book.objects.all()
    graph = {}

    # Préparer les sets de mots pour chaque livre
    book_words = {}
    for book in books:
        words = set(book.title.lower().split())  # on peut étendre au texte complet si disponible
        words = {w for w in words if len(w) > 3}
        book_words[book.id] = words
        graph[book.id] = set()

    # Calculer similarité Jaccard et créer les liens
    for b1 in books:
        for b2 in books:
            if b1.id >= b2.id:
                continue  # éviter doublons et auto-lien

            # Similarité Jaccard
            w1, w2 = book_words[b1.id], book_words[b2.id]
            if not w1 or not w2:
                continue
            intersection = len(w1 & w2)
            union = len(w1 | w2)
            jaccard = intersection / union if union > 0 else 0

            if jaccard > 0.1:  # seuil de similarité
                graph[b1.id].add(b2.id)
                graph[b2.id].add(b1.id)

    # Convert sets → lists pour JSON
    graph = {str(k): list(v) for k, v in graph.items()}
    GRAPH_FILE.write_text(json.dumps(graph), encoding="utf-8")
    print("✅ Graphe sauvegardé dans", GRAPH_FILE)

    return graph


def load_graph():
    if GRAPH_FILE.exists():
        return json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
    else:
        return build_graph_from_books()


# -------------------------
# Calcul de centralité manuelle
# -------------------------
def compute_centrality_for_ids(book_ids, method="closeness"):
    graph_data = load_graph()
    # Construire le graphe complet comme dict[int, set[int]]
    full_graph = {int(k): set(v) for k, v in graph_data.items()}

    # Nombre de nœuds total dans le graphe (pour normalisation)
    N_total = len(full_graph)

    centrality_scores = {}

    if method == "closeness":
        for node in book_ids:
            node = int(node)
            if node not in full_graph:
                centrality_scores[node] = 0
                continue

            # distances dans la composante de `node` (BFS sur le graphe complet)
            distances = bfs_distances(full_graph, node)  # dict {n: dist}
            reachable = len(distances)  # inclut node lui-même

            if reachable <= 1:
                centrality_scores[node] = 0
                continue

            # somme des distances vers tous les autres noeuds atteignables
            total_dist = sum(distances.values())

            if total_dist <= 0:
                centrality_scores[node] = 0
                continue

            # closeness "raw" sur la composante : (reachable-1) / sumdist
            raw = (reachable - 1) / total_dist

            # normalisation pour graphe disconnexe (comme NetworkX)
            # multiplie par (reachable-1)/(N_total-1) pour tenir compte de la taille globale
            if N_total > 1:
                norm = raw * ((reachable - 1) / (N_total - 1))
            else:
                norm = 0.0

            centrality_scores[node] = norm

    elif method == "betweenness":
        # placeholder simple (tu peux remplacer par une version exacte)
        centrality_scores = {int(n): 0 for n in book_ids if int(n) in full_graph}

    elif method == "pagerank":
        # PageRank calculé sur le graphe complet ; on renvoie seulement les ids demandés
        pr = pagerank({k: v for k, v in full_graph.items()})
        centrality_scores = {int(n): pr.get(int(n), 0.0) for n in book_ids}

    else:
        centrality_scores = {int(n): 0 for n in book_ids if int(n) in full_graph}

    return centrality_scores
    
def bfs_distances(graph, start):
    visited = {start: 0}
    queue = [start]
    while queue:
        node = queue.pop(0)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited[neighbor] = visited[node] + 1
                queue.append(neighbor)
    return visited


def pagerank(graph, d=0.85, max_iter=20):
    N = len(graph)
    ranks = {node: 1/N for node in graph}
    for _ in range(max_iter):
        new_ranks = {}
        for node in graph:
            rank_sum = sum(ranks[neighbor] / len(graph[neighbor]) for neighbor in graph if node in graph[neighbor])
            new_ranks[node] = (1 - d)/N + d * rank_sum
        ranks = new_ranks
    return ranks


# -------------------------
# Vues Django REST
# -------------------------
@api_view(["GET"])
def get_suggestions(request):
    book_id = request.GET.get("id")
    if not book_id:
        return Response({"error": "Missing id parameter"}, status=400)

    graph = load_graph()
    suggestions = graph.get(book_id, [])
    books = []
    for s_id in suggestions:
        try:
            b = Book.objects.get(id=s_id)
            books.append({
                "id": b.id,
                "title": b.title,
                "author": b.author,
                "image_url": b.image_url,
            })
        except Book.DoesNotExist:
            continue
    ref = Book.objects.get(id=book_id)
    return Response({"id": book_id, "title": ref.title, "results": books})


@api_view(["GET"])
def enhanced_search(request):
    pattern = request.GET.get("q", "")
    page = int(request.GET.get("page", 1))
    size = int(request.GET.get("size", 10))
    regex_mode = request.GET.get("regex", "false").lower() == "true"
    centrality_enabled = request.GET.get("centrality", "false").lower() == "true"

    if not pattern:
        return JsonResponse({"page": page, "size": size, "total": 0, "results": []})

    data = perform_search_logic(pattern, page=page, size=size, regex=regex_mode)

    ids = [r["id"] for r in data["results"] if r["id"]]

    if centrality_enabled and ids:
        centrality_scores = compute_centrality_for_ids(ids)
        for r in data["results"]:
            bid = r["id"]
            r["score"] = r.get("score", 0) + centrality_scores.get(bid, 0)
        data["results"].sort(key=lambda x: -x.get("score", 0))

    return JsonResponse(data)

def perform_search_logic(query, page=1, size=10, regex=False):
    """
    Retourne le dict {page, size, total, results} comme search_books ou search_regex.
    """
    start = (page - 1) * size

    if regex:
        # Search regex
        body = {
            "query": {
                "regexp": {
                    "term": {"value": query.lower()}
                }
            },
            "size": 10000
        }
    else:
        # Search normal
        body = {
            "from": 0,
            "size": 1000,
            "query": {
                "regexp": {  # ou match selon ton search_books
                    "term": {"value": query.lower()}
                }
            }
        }

    res = es.search(index=INDEX_NAME, body=body)
    hits = res["hits"]["hits"]

    # Récupérer book_ids et occurrences
    book_map = {}
    for hit in hits:
        for bid, count in hit["_source"]["books"].items():
            bid_int = int(bid)
            book_map[bid_int] = book_map.get(bid_int, 0) + count

    total = len(book_map)
    if total == 0:
        return {"page": page, "size": size, "total": 0, "results": []}

    # Pagination
    sorted_books = sorted(book_map.items(), key=lambda x: -x[1])
    paginated = sorted_books[start:start + size]

    book_ids = [bid for bid, _ in paginated]
    books = Book.objects.filter(id__in=book_ids)
    books_dict = {book.id: book for book in books}

    results = []
    for bid, occ in paginated:
        book = books_dict.get(bid)
        if book:
            results.append({
                "id": book.id,
                "title": book.title,
                "author": book.author,
                "image_url": book.image_url,
                "score": occ
            })

    return {"page": page, "size": size, "total": total, "results": results}
