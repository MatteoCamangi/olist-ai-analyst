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
| `assumptions` | list of strings | Required. Can be empty (`[]`). Lists every filter, date range and definition chosen. |
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
  how ties were broken, which definition of a vague term was used.

## When the data cannot answer

If the question cannot be answered from the available data, set `"status": "cannot_answer"`, set `"answer": null`
and explain why in `reason`. Never invent, estimate or extrapolate numbers to fill the gap. You can suggest
a related question the data can answer in the text or in `reason`, but `answer` stays `null`.

## Worked examples

The values in these examples are illustrative values, not real data. They show the shape of an answer only.

### Numeric answer (illustrative values, not real data)

> In a fictional shop, how many orders were placed last month, how many were canceled, and what share is that?

The shop received 1,234 orders last month, and 56 of them were canceled, which is 4.54% of all orders.

```json
{
  "status": "answered",
  "assumptions": [
    "Last month means the calendar month before the as-of date, by purchase date",
    "Orders counts every order placed, whatever its status",
    "Share is canceled orders divided by all orders placed"
  ],
  "answer": {
    "total_orders": 1234,
    "canceled_orders": 56,
    "canceled_share_pct": 4.54
  }
}
```

### Ranking answer (illustrative values, not real data)

> In a fictional shop, which three product categories had the most orders?

Moon boots lead with 321 orders, followed by glow mugs (210) and cloud lamps (98).

```json
{
  "status": "answered",
  "assumptions": [
    "An order counts once per category, even with several items of that category",
    "Ties would be broken by category name"
  ],
  "answer": {
    "rows": [
      {"category": "moon_boots", "orders": 321},
      {"category": "glow_mugs", "orders": 210},
      {"category": "cloud_lamps", "orders": 98}
    ]
  }
}
```

### Set answer (illustrative values, fake ids)

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
