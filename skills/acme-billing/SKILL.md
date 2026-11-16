---
name: acme-billing
description: ACME Corp billing rules - invoice ID format, late fees, fiscal quarters, invoice JSON schema
---
# ACME billing rules

## Invoice ID
Format: `ACME-<YY>-<CUS>-<NNNN>`
- `YY`: last two digits of the year the invoice is issued
- `CUS`: first three letters of the customer name, uppercase
- `NNNN`: the customer's invoice sequence number, zero-padded to 4 digits
Example: customer "Initech", sequence 12, issued 2024-11-02 -> `ACME-24-INI-0012`

## Late fee
2% of the invoice amount for every *started* week the payment is late, capped at 10%.
Example: 8 days late = 2 started weeks = 4%.

## Fiscal year
The ACME fiscal year starts on 1 March and is named after the calendar year it starts in.
Quarters: Q1 = Mar-May, Q2 = Jun-Aug, Q3 = Sep-Nov, Q4 = Dec-Feb.
Written as `FY<YY>-Q<n>`, e.g. 2024-12-05 -> `FY24-Q4`, 2025-01-20 -> `FY24-Q4`.

## Invoice JSON schema
```json
{"invoice_id": "<ID as above>", "customer": "<name>", "total_cents": <integer>, "currency": "USD"}
```
`total_cents` is the sum of line amounts in cents (integer, no decimals).
