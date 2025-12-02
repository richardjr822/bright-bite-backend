# BrightBite Wallet System

## Overview

The wallet system provides a secure, testable payment infrastructure for the BrightBite campus food ordering platform.

## Features

- **Wallet Management**: Each user has a wallet with a balance
- **Top-Up**: Add funds via GCash/Maya or sandbox mode
- **Debit/Spend**: Pay for orders from wallet balance
- **Refunds**: Process refunds for cancelled orders
- **Sandbox Mode**: Test payments without real money (enabled by default in development)
- **Security**: Wallet freeze functionality, PIN-protected sandbox mode

## Database Setup

Run the SQL migration in your Supabase SQL Editor:

```bash
# Copy and paste the contents of 001_wallet_tables.sql into Supabase SQL Editor
```

## Environment Variables

Add these to your `.env` file:

```env
# Wallet Configuration
WALLET_SANDBOX_MODE=true          # Enable sandbox/test mode (default: true)
WALLET_SANDBOX_PIN=1234           # PIN for sandbox top-ups (default: 1234)

# Production Payment Gateway (optional)
GCASH_MERCHANT_ID=your_merchant_id
GCASH_MERCHANT_NAME=BrightBite
GCASH_PD_CODE=your_pd_code
GCASH_WEBHOOK_SECRET=your_webhook_secret

MAYA_WEBHOOK_SECRET=your_maya_webhook_secret
```

## API Endpoints

### Wallet Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/wallet` | GET | Get user's wallet balance |
| `/api/wallet/transactions` | GET | List transaction history |

### Top-Up

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/wallet/top-up` | POST | Initiate top-up via GCash/Maya |
| `/api/wallet/confirm` | POST | Confirm pending top-up |
| `/api/wallet/status` | GET | Check transaction status |

### Sandbox (Test Mode)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/wallet/sandbox/status` | GET | Check if sandbox mode is enabled |
| `/api/wallet/sandbox/top-up` | POST | Instant test top-up (requires PIN) |

### Spending

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/wallet/debit` | POST | Deduct from wallet (for orders) |
| `/api/wallet/refund` | POST | Refund to wallet |

### Webhooks (Production)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/wallet/webhook/gcash` | POST | GCash payment webhook |
| `/api/wallet/webhook/maya` | POST | Maya payment webhook |

## Usage Examples

### Sandbox Top-Up (Testing)

```javascript
const response = await fetch('/api/wallet/sandbox/top-up', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`
  },
  body: JSON.stringify({
    amount: 500,
    pin: '1234',
    description: 'Test top-up'
  })
});
```

### Debit/Spend

```javascript
const response = await fetch('/api/wallet/debit', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`
  },
  body: JSON.stringify({
    amount: 150,
    description: 'Order #12345 payment',
    order_id: 'uuid-of-order'
  })
});
```

### Refund

```javascript
const response = await fetch('/api/wallet/refund', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`
  },
  body: JSON.stringify({
    amount: 150,
    reason: 'Order cancelled by customer',
    order_id: 'uuid-of-order'
  })
});
```

## Security Considerations

1. **Sandbox Mode**: Disabled in production (`WALLET_SANDBOX_MODE=false`)
2. **PIN Protection**: Sandbox requires a PIN even in test mode
3. **Webhook Verification**: HMAC signature verification for payment webhooks
4. **Balance Validation**: Debit operations verify sufficient balance
5. **Wallet Freeze**: Users can freeze their wallet for security
6. **Transaction Limits**: Single transaction limit of ₱50,000

## Testing Flow

1. Start the backend: `python -m uvicorn app.main:app --reload`
2. Start the frontend: `npm run dev`
3. Log in as a student
4. Navigate to "My Wallet"
5. Click "Top Up Wallet"
6. Toggle "Test Mode" button
7. Enter amount and PIN (default: 1234)
8. Click "Test Top Up"
9. Balance updates instantly!

## Production Deployment

1. Set `WALLET_SANDBOX_MODE=false`
2. Configure real GCash/Maya credentials
3. Set up webhook URLs in payment provider dashboards
4. Test with actual payment flow
