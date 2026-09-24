// Client for the seven public registration routes (PYTHON/routes/inregistrare/README.md).
// Every failure becomes an ApiError carrying the server's Romanian sentence and its
// ASCII reason code, so the wizard can branch on the code and show the sentence.

const BASE = '/api/inregistrare';
const TOKEN_HEADER = 'X-Registration-Token';

export class ApiError extends Error {
  constructor(status, reason, message) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.reason = reason;
  }
}

export function createApi(getToken) {
  async function call(method, path, body) {
    const headers = { Accept: 'application/json' };
    const token = getToken();
    if (token) headers[TOKEN_HEADER] = token;
    if (body !== undefined) headers['Content-Type'] = 'application/json';

    let response;
    try {
      response = await fetch(BASE + path, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: 'same-origin',
        cache: 'no-store',
      });
    } catch (err) {
      console.error(`[inregistrare] ${method} ${path} did not reach the server`, err);
      throw new ApiError(
        0,
        'NETWORK',
        'Serverul nu răspunde. Verificați conexiunea la internet și reîncercați.'
      );
    }

    // nginx answers a dead upstream with an HTML page, so a body that is not JSON is
    // reported by its status rather than shown.
    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        console.error(`[inregistrare] ${method} ${path} answered non-JSON (${response.status})`, err);
      }
    }

    if (!response.ok) {
      if (data && data.error) {
        throw new ApiError(response.status, data.reason || 'UNKNOWN', data.error);
      }
      throw new ApiError(
        response.status,
        'UNEXPECTED',
        `Serverul a răspuns neașteptat (cod ${response.status}). Reîncercați mai târziu.`
      );
    }
    if (data === null) {
      throw new ApiError(
        response.status,
        'UNEXPECTED',
        'Serverul a trimis un răspuns gol. Reîncercați mai târziu.'
      );
    }
    return data;
  }

  return {
    anaf: (cf) => call('POST', '/anaf', { cf }),
    sendCode: (email) => call('POST', '/cod', { email }),
    verifyCode: (cod) => call('POST', '/verifica', { cod }),
    sursaSector: () => call('GET', '/sursasector'),
    clasificatii: (tip) => call('GET', `/clasificatii?tip=${encodeURIComponent(tip)}`),
    dbName: (denumire) => call('GET', `/nume?denumire=${encodeURIComponent(denumire)}`),
    submit: (payload) => call('POST', '/cerere', payload),
    // The password link of slice 0075-03 (parola.js). Its token travels in the body,
    // not in the registration header: it is a different token with a different life.
    linkState: (token) => call('POST', '/parola/stare', { token }),
    setPassword: (token, parola) => call('POST', '/parola', { token, parola }),
  };
}
