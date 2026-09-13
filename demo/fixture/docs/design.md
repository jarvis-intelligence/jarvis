# Auth module design

The `AuthService` class handles credential validation and session token
issuance. It delegates identity modeling to `AuthServiceUser`.

> Note: "AuthService" also appears in README.md — grep will match it there too.
