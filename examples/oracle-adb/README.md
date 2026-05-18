# Oracle Autonomous Database Examples

These examples show Oracle Autonomous Database connection shapes. They are
templates only. Do not commit downloaded wallet files, database passwords, or
wallet passwords.

Store local ADB material in an ignored directory such as:

```bash
mkdir -p .local/oracle-adb/wallet
unzip Wallet_MYDB.zip -d .local/oracle-adb/wallet
```

For walletless TLS, configure Autonomous Database for access from your client
IP or VCN and use `config-walletless-tls.json`.

For wallet/mTLS, unzip the wallet into `.local/oracle-adb/wallet`, set both
environment variables, and use `config-mtls-wallet.json`:

```bash
export ORACLE_PASSWORD='replace-with-database-user-password'
export ORACLE_WALLET_PASSWORD='replace-with-wallet-password'
nats-sink test-sink examples/oracle-adb/config-mtls-wallet.json
```

