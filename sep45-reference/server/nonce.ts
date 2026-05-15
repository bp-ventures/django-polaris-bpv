export async function generateNonce(key: string): Promise<string> {
  const kv = await Deno.openKv();
  // generate a random u32
  const nonce = new Uint32Array(1);
  crypto.getRandomValues(nonce);

  // Store nonce
  try {
    await kv.set(
      ["nonce", key, nonce[0].toString()],
      { used: false },
      { expireIn: 300 * 1000 },
    );
  } finally {
    kv.close();
  }

  return nonce[0].toString();
}

export async function verifyNonce(
  key: string,
  nonce: string,
): Promise<boolean> {
  const kv = await Deno.openKv();

  try {
    const storedNonce = await kv.get<{ used: boolean }>([
      "nonce",
      key,
      nonce,
    ]);

    if (storedNonce.value === null || storedNonce.value.used) {
      return false;
    }

    // Mark nonce as used
    await kv.set(["nonce", key, nonce], { used: true });
  } finally {
    kv.close();
  }

  return true;
}
