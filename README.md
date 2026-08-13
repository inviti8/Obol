# Obol

An **MCP server that gives an AI agent a disposable Algorand wallet**, so it can
pay for [x402](https://x402.org) resources without a human provisioning anything.

The name is the coin paid to the ferryman for passage: small denomination, one
purpose, spent and gone.

```
agent → x402_fetch(url) → 402 → sign → pay → body
```

x402 has 1,204 listed resources and almost no buyers — 13 wallets account for 90%
of all volume, and they are scripted loops rather than agents. The rail exists;
nothing can reach it. Obol is the buyer-side piece.

- **[CLAUDE.md](./CLAUDE.md)** — orientation, and the x402 facts that cost real
  debugging time to learn.
- **[DESIGN.md](./DESIGN.md)** — architecture, security model, v1 scope.

Status: **design only, nothing built.**
