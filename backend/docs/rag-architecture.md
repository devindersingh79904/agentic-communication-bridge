# Trybo Agentic Bridge - RAG & Vector Store Architecture

This document describes the design, schema, and lifecycle of the Retrieval-Augmented Generation (RAG) and Vector Store components in the backend.

---

## 1. Design Overview

To recommend accurate suppliers, the system uses semantic vector search against local vendor catalogs. This is powered by **ChromaDB**, an open-source AI vector database.

- **Storage Location**: Persisted locally in the path specified by the `CHROMA_PERSIST_PATH` environment variable (defaults to `./chroma_db`).
- **Core Collection**: All vendors are stored in a single ChromaDB collection named `procurement_vendors`.
- **Lazy Loading**: The database connection and collection are initialized on-demand when the first search request is received, keeping startup fast and lightweight.

---

## 2. Vendor Data Schema

Mock vendor data is stored in the structured JSON file [`sample_vendors.json`](file:///Users/dsp/development/assignment/backend/app/rag/sample_vendors.json). It contains exactly **40 mock vendors** (10 vendors per procurement category: `computer`, `transport`, `food`, and `stationery`).

### Vendor JSON Attributes:
| Attribute | Type | Description |
|---|---|---|
| `vendor_name` | `string` | The official name of the vendor |
| `category` | `string` | Exactly one of: `computer`, `transport`, `food`, `stationery` |
| `items` | `array[dict]` | Catalog items with `name` and `price` (in INR) |
| `rating` | `float` | Quality rating from 0.0 to 5.0 |
| `delivery_days`| `integer` | Standard turnaround time in days |
| `location` | `string` | Location of the warehouse/office (e.g., Koramangala, Bellandur) |
| `metadata` | `dict` | Nested dictionary containing the `description` string |

---

## 3. Seeding & Database Migrations

During `init_vector_store()`, the system manages dynamic seeding and upgrades:
1. It retrieves the current record count in the `procurement_vendors` collection via `store.collection.count()`.
2. **First-Time Seeding**: If the database is empty (`count == 0`), it seeds the 40 mock vendors from `sample_vendors.json`.
3. **Automatic Upgrades / Migrations**: If the database exists but contains only the old configuration size of `12` vendors, the system automatically drops the old collection, recreates it, and seeds the full set of `40` vendors. This guarantees that developers receive data upgrades without manually deleting folders.

---

## 4. Vector Embedding Generation

Before storing records in ChromaDB, vendor metadata is flattened into a dense summary document:

```python
def make_vendor_text(vendor: Dict[str, Any]) -> str:
    items_str = ", ".join([f"{i['name']} (${i['price']})" for i in vendor["items"]])
    desc = vendor["metadata"].get("description", "")
    return (
        f"Vendor Name: {vendor['vendor_name']}. "
        f"Category: {vendor['category']}. "
        f"Location: {vendor['location']}. "
        f"Items: {items_str}. "
        f"Rating: {vendor['rating']}. "
        f"Delivery: {vendor['delivery_days']} days. "
        f"Description: {desc}"
    )
```

The resulting string is converted into a vector representation using the configured embedding service (`get_embedding(text)`) and added to ChromaDB along with the original metadata dictionary.

---

## 5. Query and Retrieval Pipeline

When a user submits a prompt, the orchestrator triggers the retrieval pipeline:

1. **Category Isolation**:
   The system first extracts the category (via python keyword matching or LLM classification).
2. **ChromaDB Metadata Filtering**:
   If a category is identified, it is passed to the Chroma query as a filter constraint (`where={"category": category}`). This isolates search results, avoiding category mixing (e.g., getting food recommendations for laptop requests).
3. **Similarity Score Calculation**:
   ChromaDB uses L2 squared distance. To convert this into a standard confidence score between `0.0` and `1.0`, the system maps the distance:
   $$\text{Similarity} = \frac{1.0}{1.0 + \text{Distance}}$$
4. **Fallback Mechanism**:
   If ChromaDB fails, isn't installed, or encounters an exception, the system falls back to a python-native `PurePythonVectorStore` or a structured static subset of `SAMPLE_VENDORS` to ensure the application remains functional.
