# Nessie request/response evidence

All authentication keys are masked. Headers and JSON bodies are the actual values recorded by the HTTP transport.
The first three diagnostic pairs are attempts (a), (b), and (c), in that order.

## Ordered diagnostics

### Pair 1

```http
POST https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07/transfers?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
content-length: 199
content-type: application/json
```

Request JSON:

```json
{
  "amount": 1.0,
  "transaction_date": "2026-10-24",
  "description": "Countersign transfer schema diagnostic; USD 1.00",
  "medium": "balance",
  "payee_id": "c60e8616-4524-4005-af34-b28326d730bb",
  "status": "pending"
}
```

Response:

```json
{
  "status": 400,
  "body": "2 validation errors for TransferCreate\nmedium\n  extra fields not permitted (type=value_error.extra)\npayee_id\n  extra fields not permitted (type=value_error.extra)"
}
```

### Pair 2

```http
POST https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07/transfers?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
content-length: 130
content-type: application/json
```

Request JSON:

```json
{
  "amount": 1.0,
  "transaction_date": "2026-10-24",
  "description": "Countersign transfer schema diagnostic; USD 1.00",
  "status": "pending"
}
```

Response:

```json
{
  "status": 201,
  "body": {
    "code": 201,
    "message": "Transfer created",
    "objectCreated": {
      "transaction_date": "2026-10-24",
      "status": "pending",
      "amount": 1,
      "description": "Countersign transfer schema diagnostic; USD 1.00",
      "_id": "307dab26-4f26-4ef5-9f95-5fe21ce65cd9"
    }
  }
}
```

### Pair 3

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07/transfers?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": [
    {
      "id": "307dab26-4f26-4ef5-9f95-5fe21ce65cd9",
      "transaction_date": "2026-10-24",
      "status": "pending",
      "amount": 1,
      "description": "Countersign transfer schema diagnostic; USD 1.00"
    }
  ]
}
```

### Pair 4

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "type": "Checking",
    "nickname": "Countersign:fogline-festival:checking",
    "rewards": 0,
    "balance": 250000,
    "account_number": "8257956061215072",
    "customer_id": "aec49fb4-beb6-4267-8488-c5277aceec29"
  }
}
```

### Pair 5

```http
GET https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "c60e8616-4524-4005-af34-b28326d730bb",
    "type": "Checking",
    "nickname": "Countersign:VEN-001:checking",
    "rewards": 0,
    "balance": 0,
    "account_number": "1946035628697620",
    "customer_id": "fe1e3074-86e1-4c84-9e21-1eba518919b5"
  }
}
```

### Pair 6

```http
POST https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07/withdrawals?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
content-length: 159
content-type: application/json
```

Request JSON:

```json
{
  "amount": 1.0,
  "transaction_date": "2026-10-24",
  "description": "Countersign paired withdrawal/deposit diagnostic; USD 1.00",
  "medium": "balance",
  "status": "pending"
}
```

Response:

```json
{
  "status": 201,
  "body": {
    "code": 201,
    "message": "Withdrawal created",
    "objectCreated": {
      "medium": "balance",
      "transaction_date": "2026-10-24",
      "status": "pending",
      "amount": 1,
      "description": "Countersign paired withdrawal/deposit diagnostic; USD 1.00",
      "_id": "94c14732-1b6b-4194-81ff-09d4951c564a",
      "creation_date": "2026-10-24",
      "type": "withdrawal",
      "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07"
    }
  }
}
```

### Pair 7

```http
POST https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb/deposits?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
content-length: 189
content-type: application/json
```

Request JSON:

```json
{
  "amount": 1.0,
  "transaction_date": "2026-10-24",
  "description": "Countersign paired deposit for withdrawal 94c14732-1b6b-4194-81ff-09d4951c564a; USD 1.00",
  "medium": "balance",
  "status": "pending"
}
```

Response:

```json
{
  "status": 201,
  "body": {
    "code": 201,
    "message": "Deposit created",
    "objectCreated": {
      "medium": "balance",
      "transaction_date": "2026-10-24",
      "status": "pending",
      "amount": 1,
      "description": "Countersign paired deposit for withdrawal 94c14732-1b6b-4194-81ff-09d4951c564a; USD 1.00",
      "_id": "dc37c1bb-113c-4239-a669-a73ea68c6695",
      "creation_date": "2026-10-24"
    }
  }
}
```

### Pair 8

```http
GET https://api.nessieisreal.com/withdrawal/94c14732-1b6b-4194-81ff-09d4951c564a?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "94c14732-1b6b-4194-81ff-09d4951c564a",
    "type": "withdrawal",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "medium": "balance",
    "amount": 1,
    "description": "Countersign paired withdrawal/deposit diagnostic; USD 1.00"
  }
}
```

### Pair 9

```http
GET https://api.nessieisreal.com/deposits/dc37c1bb-113c-4239-a669-a73ea68c6695?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "dc37c1bb-113c-4239-a669-a73ea68c6695",
    "medium": "balance",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "amount": 1,
    "description": "Countersign paired deposit for withdrawal 94c14732-1b6b-4194-81ff-09d4951c564a; USD 1.00"
  }
}
```

### Pair 10

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "type": "Checking",
    "nickname": "Countersign:fogline-festival:checking",
    "rewards": 0,
    "balance": 250000,
    "account_number": "8257956061215072",
    "customer_id": "aec49fb4-beb6-4267-8488-c5277aceec29"
  }
}
```

### Pair 11

```http
GET https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "c60e8616-4524-4005-af34-b28326d730bb",
    "type": "Checking",
    "nickname": "Countersign:VEN-001:checking",
    "rewards": 0,
    "balance": 0,
    "account_number": "1946035628697620",
    "customer_id": "fe1e3074-86e1-4c84-9e21-1eba518919b5"
  }
}
```

### Pair 12

```http
GET https://api.nessieisreal.com/documentation?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 403,
  "body": {
    "message": "Missing Authentication Token"
  }
}
```

### Pair 13

```http
GET https://api.nessieisreal.com/openapi.json?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 403,
  "body": {
    "message": "Missing Authentication Token"
  }
}
```

### Pair 14

```http
GET https://api.nessieisreal.com/swagger.json?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 403,
  "body": {
    "message": "Missing Authentication Token"
  }
}
```

## Live acceptance test and idempotent readbacks

### Pair 1

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "type": "Checking",
    "nickname": "Countersign:fogline-festival:checking",
    "rewards": 0,
    "balance": 250000,
    "account_number": "8257956061215072",
    "customer_id": "aec49fb4-beb6-4267-8488-c5277aceec29"
  }
}
```

### Pair 2

```http
GET https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "c60e8616-4524-4005-af34-b28326d730bb",
    "type": "Checking",
    "nickname": "Countersign:VEN-001:checking",
    "rewards": 0,
    "balance": 0,
    "account_number": "1946035628697620",
    "customer_id": "fe1e3074-86e1-4c84-9e21-1eba518919b5"
  }
}
```

### Pair 3

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "type": "Checking",
    "nickname": "Countersign:fogline-festival:checking",
    "rewards": 0,
    "balance": 250000,
    "account_number": "8257956061215072",
    "customer_id": "aec49fb4-beb6-4267-8488-c5277aceec29"
  }
}
```

### Pair 4

```http
GET https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "c60e8616-4524-4005-af34-b28326d730bb",
    "type": "Checking",
    "nickname": "Countersign:VEN-001:checking",
    "rewards": 0,
    "balance": 0,
    "account_number": "1946035628697620",
    "customer_id": "fe1e3074-86e1-4c84-9e21-1eba518919b5"
  }
}
```

### Pair 5

```http
POST https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07/withdrawals?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
content-length: 144
content-type: application/json
```

Request JSON:

```json
{
  "medium": "balance",
  "transaction_date": "2026-10-24",
  "status": "pending",
  "amount": 1.0,
  "description": "Countersign B3 live $1.00 paired smoke test"
}
```

Response:

```json
{
  "status": 201,
  "body": {
    "code": 201,
    "message": "Withdrawal created",
    "objectCreated": {
      "medium": "balance",
      "transaction_date": "2026-10-24",
      "status": "pending",
      "amount": 1,
      "description": "Countersign B3 live $1.00 paired smoke test",
      "_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
      "creation_date": "2026-10-24",
      "type": "withdrawal",
      "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07"
    }
  }
}
```

### Pair 6

```http
POST https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb/deposits?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
content-length: 144
content-type: application/json
```

Request JSON:

```json
{
  "medium": "balance",
  "transaction_date": "2026-10-24",
  "status": "pending",
  "amount": 1.0,
  "description": "Countersign B3 live $1.00 paired smoke test"
}
```

Response:

```json
{
  "status": 201,
  "body": {
    "code": 201,
    "message": "Deposit created",
    "objectCreated": {
      "medium": "balance",
      "transaction_date": "2026-10-24",
      "status": "pending",
      "amount": 1,
      "description": "Countersign B3 live $1.00 paired smoke test",
      "_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
      "creation_date": "2026-10-24"
    }
  }
}
```

### Pair 7

```http
GET https://api.nessieisreal.com/withdrawal/396d1735-ce3a-4ffb-b2b4-4fbc389260bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
    "type": "withdrawal",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "medium": "balance",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 8

```http
GET https://api.nessieisreal.com/deposits/17adfb45-eed0-48f0-bd6b-d346b058a0bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
    "medium": "balance",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 9

```http
GET https://api.nessieisreal.com/withdrawal/396d1735-ce3a-4ffb-b2b4-4fbc389260bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
    "type": "withdrawal",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "medium": "balance",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 10

```http
GET https://api.nessieisreal.com/deposits/17adfb45-eed0-48f0-bd6b-d346b058a0bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
    "medium": "balance",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 11

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "type": "Checking",
    "nickname": "Countersign:fogline-festival:checking",
    "rewards": 0,
    "balance": 250000,
    "account_number": "8257956061215072",
    "customer_id": "aec49fb4-beb6-4267-8488-c5277aceec29"
  }
}
```

### Pair 12

```http
GET https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "c60e8616-4524-4005-af34-b28326d730bb",
    "type": "Checking",
    "nickname": "Countersign:VEN-001:checking",
    "rewards": 0,
    "balance": 0,
    "account_number": "1946035628697620",
    "customer_id": "fe1e3074-86e1-4c84-9e21-1eba518919b5"
  }
}
```

### Pair 13

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "type": "Checking",
    "nickname": "Countersign:fogline-festival:checking",
    "rewards": 0,
    "balance": 250000,
    "account_number": "8257956061215072",
    "customer_id": "aec49fb4-beb6-4267-8488-c5277aceec29"
  }
}
```

### Pair 14

```http
GET https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "c60e8616-4524-4005-af34-b28326d730bb",
    "type": "Checking",
    "nickname": "Countersign:VEN-001:checking",
    "rewards": 0,
    "balance": 0,
    "account_number": "1946035628697620",
    "customer_id": "fe1e3074-86e1-4c84-9e21-1eba518919b5"
  }
}
```

### Pair 15

```http
GET https://api.nessieisreal.com/withdrawal/396d1735-ce3a-4ffb-b2b4-4fbc389260bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
    "type": "withdrawal",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "medium": "balance",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 16

```http
GET https://api.nessieisreal.com/deposits/17adfb45-eed0-48f0-bd6b-d346b058a0bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
    "medium": "balance",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 17

```http
GET https://api.nessieisreal.com/withdrawal/396d1735-ce3a-4ffb-b2b4-4fbc389260bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
    "type": "withdrawal",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "medium": "balance",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 18

```http
GET https://api.nessieisreal.com/deposits/17adfb45-eed0-48f0-bd6b-d346b058a0bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
    "medium": "balance",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 19

```http
GET https://api.nessieisreal.com/withdrawal/396d1735-ce3a-4ffb-b2b4-4fbc389260bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
    "type": "withdrawal",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "medium": "balance",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 20

```http
GET https://api.nessieisreal.com/deposits/17adfb45-eed0-48f0-bd6b-d346b058a0bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
    "medium": "balance",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 21

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "type": "Checking",
    "nickname": "Countersign:fogline-festival:checking",
    "rewards": 0,
    "balance": 250000,
    "account_number": "8257956061215072",
    "customer_id": "aec49fb4-beb6-4267-8488-c5277aceec29"
  }
}
```

### Pair 22

```http
GET https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "c60e8616-4524-4005-af34-b28326d730bb",
    "type": "Checking",
    "nickname": "Countersign:VEN-001:checking",
    "rewards": 0,
    "balance": 0,
    "account_number": "1946035628697620",
    "customer_id": "fe1e3074-86e1-4c84-9e21-1eba518919b5"
  }
}
```

### Pair 23

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "type": "Checking",
    "nickname": "Countersign:fogline-festival:checking",
    "rewards": 0,
    "balance": 250000,
    "account_number": "8257956061215072",
    "customer_id": "aec49fb4-beb6-4267-8488-c5277aceec29"
  }
}
```

### Pair 24

```http
GET https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "c60e8616-4524-4005-af34-b28326d730bb",
    "type": "Checking",
    "nickname": "Countersign:VEN-001:checking",
    "rewards": 0,
    "balance": 0,
    "account_number": "1946035628697620",
    "customer_id": "fe1e3074-86e1-4c84-9e21-1eba518919b5"
  }
}
```

### Pair 25

```http
GET https://api.nessieisreal.com/withdrawal/396d1735-ce3a-4ffb-b2b4-4fbc389260bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
    "type": "withdrawal",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "medium": "balance",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 26

```http
GET https://api.nessieisreal.com/deposits/17adfb45-eed0-48f0-bd6b-d346b058a0bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
    "medium": "balance",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 27

```http
GET https://api.nessieisreal.com/withdrawal/396d1735-ce3a-4ffb-b2b4-4fbc389260bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
    "type": "withdrawal",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "medium": "balance",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 28

```http
GET https://api.nessieisreal.com/deposits/17adfb45-eed0-48f0-bd6b-d346b058a0bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
    "medium": "balance",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 29

```http
GET https://api.nessieisreal.com/withdrawal/396d1735-ce3a-4ffb-b2b4-4fbc389260bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
    "type": "withdrawal",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "medium": "balance",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 30

```http
GET https://api.nessieisreal.com/deposits/17adfb45-eed0-48f0-bd6b-d346b058a0bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
    "medium": "balance",
    "transaction_date": "2026-10-24",
    "status": "pending",
    "amount": 1,
    "description": "Countersign B3 live $1.00 paired smoke test"
  }
}
```

### Pair 31

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07/withdrawals?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": [
    {
      "_id": "396d1735-ce3a-4ffb-b2b4-4fbc389260bb",
      "type": "withdrawal",
      "transaction_date": "2026-10-24",
      "status": "pending",
      "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
      "medium": "balance",
      "amount": 1,
      "description": "Countersign B3 live $1.00 paired smoke test"
    },
    {
      "_id": "94c14732-1b6b-4194-81ff-09d4951c564a",
      "type": "withdrawal",
      "transaction_date": "2026-10-24",
      "status": "pending",
      "payer_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
      "medium": "balance",
      "amount": 1,
      "description": "Countersign paired withdrawal/deposit diagnostic; USD 1.00"
    }
  ]
}
```

### Pair 32

```http
GET https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb/deposits?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": [
    {
      "_id": "17adfb45-eed0-48f0-bd6b-d346b058a0bb",
      "medium": "balance",
      "transaction_date": "2026-10-24",
      "status": "pending",
      "amount": 1,
      "description": "Countersign B3 live $1.00 paired smoke test"
    },
    {
      "_id": "dc37c1bb-113c-4239-a669-a73ea68c6695",
      "medium": "balance",
      "transaction_date": "2026-10-24",
      "status": "pending",
      "amount": 1,
      "description": "Countersign paired deposit for withdrawal 94c14732-1b6b-4194-81ff-09d4951c564a; USD 1.00"
    }
  ]
}
```

### Pair 33

```http
GET https://api.nessieisreal.com/accounts/5def94a8-e80a-451e-94d1-602a6d073c07?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "5def94a8-e80a-451e-94d1-602a6d073c07",
    "type": "Checking",
    "nickname": "Countersign:fogline-festival:checking",
    "rewards": 0,
    "balance": 250000,
    "account_number": "8257956061215072",
    "customer_id": "aec49fb4-beb6-4267-8488-c5277aceec29"
  }
}
```

### Pair 34

```http
GET https://api.nessieisreal.com/accounts/c60e8616-4524-4005-af34-b28326d730bb?key=%5BREDACTED%5D
host: api.nessieisreal.com
accept: */*
accept-encoding: gzip, deflate
connection: keep-alive
user-agent: python-httpx/0.28.1
```

Request JSON:

```json
null
```

Response:

```json
{
  "status": 200,
  "body": {
    "_id": "c60e8616-4524-4005-af34-b28326d730bb",
    "type": "Checking",
    "nickname": "Countersign:VEN-001:checking",
    "rewards": 0,
    "balance": 0,
    "account_number": "1946035628697620",
    "customer_id": "fe1e3074-86e1-4c84-9e21-1eba518919b5"
  }
}
```

