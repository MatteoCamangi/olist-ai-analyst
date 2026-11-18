# Answer format

Every answer from the analyst agent has two parts, in this order:

1. A short plain-language answer (a few sentences) for a human reader.
2. One JSON block, last in the message, that the grader reads.

Nothing comes after the JSON block. If a message holds more than one JSON block, only the last one is graded.

## The JSON envelope

```json
{
  "status": "answered",
  "assumptions": ["..."],
  "answer": {},
  "reason": "..."
}
```

| Field | Type | Rule |
|---|---|---|
| `status` | `"answered"` or `"cannot_answer"` | Required. |
| `assumptions` | list of strings | Required. Can be empty (`[]`). Lists every definition, filter, date range or interpretation chosen. |
| `answer` | object or `null` | An object that follows the question's `answer_schema` when `status` is `answered`. `null` when `status` is `cannot_answer`. |
| `reason` | string | Required only when `status` is `cannot_answer`: why the data cannot answer the question. Leave it out otherwise. |

## answer_schema

Every question comes with an `answer_schema`. It gives the field names and types of `answer`, never values.
Use the field names exactly as written: no renaming, no extra nesting.

| Schema notation | What to return |
|---|---|
| `field: integer` / `number` / `string` | A single JSON value of that type. |
| `rows:` with `order` and `fields` | `"rows": [ {...}, {...} ]`, a list of objects with exactly the listed fields, ranked so the first row is rank 1. |
| `max_rows: N` | No more than N rows. |
| `field: list of X (string)` | A JSON list of X values as strings. Order does not matter. |
| `answer: null, or an object with the fields you consider relevant` | Field names are up to you. Use `null` when `status` is `cannot_answer`. |

`status: answered | cannot_answer` appears in every schema: both values are always allowed.

## Value rules

- **Percentages** are numbers in percent: `37.42`, not `0.3742` and not `"37.42%"`.
- **Money** is a plain number: `1234.5`, with no currency symbol and no thousands separator.
- **Counts** are integers.
- **Dates** are strings in `YYYY-MM-DD` format.
- **Ids** are the exact strings from the data (for example `customer_unique_id`, `seller_id`, `product_id`).
- Do not round more than the data supports. Two decimals are enough for money and percentages.

## Assumptions and uncertainty

- Put everything uncertain or assumed in `assumptions`, not only in the text. The grader reads the JSON.
- Typical entries: which order statuses were included, the date range and which date column, how revenue was defined,
  how ties were broken, which interpretation of a vague term was used.

## When the data cannot answer

If the question cannot be answered from the available data, set `"status": "cannot_answer"`, set `"answer": null`
and explain why in `reason`. Never invent, estimate or extrapolate numbers to fill the gap. You can suggest
a related question the data can answer in the text or in `reason`, but `answer` stays `null`.

## Worked examples

### Numeric answer (q001)

> How many orders did Olist receive in 2017 (by purchase date)? How many of them were delivered? What was the total
> revenue of the delivered ones?

Olist received 45,101 orders in 2017, and 43,428 of them were delivered. Delivered orders brought in 5,962,902.01 in
revenue (item price only, freight excluded).

```json
{
  "status": "answered",
  "assumptions": [
    "2017 means order_purchase_timestamp between 2017-01-01 and 2017-12-31",
    "Orders counts every order placed, whatever its status",
    "Delivered means order_status = 'delivered'",
    "Revenue is the sum of item price on delivered orders, freight excluded"
  ],
  "answer": {
    "orders_cnt": 45101,
    "delivered_orders_cnt": 43428,
    "total_revenue": 5962902.01
  }
}
```

### Ranking answer (q005)

> Where do most orders come from? Show the top 5 customer cities (delivered orders only).

Sao Paulo leads by far with 15,045 delivered orders, followed by Rio de Janeiro (6,601), Belo Horizonte (2,697),
Brasilia (2,071) and Curitiba (1,489).

```json
{
  "status": "answered",
  "assumptions": [
    "Delivered orders only (order_status = 'delivered')",
    "Cities are grouped by customer_city and customer_state, so same-named cities in different states stay separate"
  ],
  "answer": {
    "rows": [
      {"customer_city": "sao paulo", "customer_state": "SP", "orders_cnt": 15045},
      {"customer_city": "rio de janeiro", "customer_state": "RJ", "orders_cnt": 6601},
      {"customer_city": "belo horizonte", "customer_state": "MG", "orders_cnt": 2697},
      {"customer_city": "brasilia", "customer_state": "DF", "orders_cnt": 2071},
      {"customer_city": "curitiba", "customer_state": "PR", "orders_cnt": 1489}
    ]
  }
}
```

### Set answer (generic, fake ids)

For a schema such as:

```yaml
answer:
  group_a: list of seller_id (string)
  group_b: list of seller_id (string)
```

Group A has 3 sellers and group B has 2.

```json
{
  "status": "answered",
  "assumptions": ["Delivered orders only", "Boundaries are inclusive"],
  "answer": {
    "group_a": ["seller_aaa111", "seller_bbb222", "seller_ccc333"],
    "group_b": ["seller_ddd444", "seller_eee555"]
  }
}
```

Return every id in each list. Do not truncate a list or replace it with a count.
