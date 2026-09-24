// Client for the operator routes (PYTHON/routes/inregistrare/operator.py, slice 0075-05).
// Same error shape as the public page: an ApiError with the server's Romanian sentence
// and its ASCII reason code.

import { ApiError } from '../inregistrare/api.js';

export { ApiError };

const BASE = '/api/operator';
const TOKEN_HEADER = 'X-Operator-Token';

export function createOperatorApi(getToken) {
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
      console.error(`[operator] ${method} ${path} did not reach the server`, err);
      throw new ApiError(0, 'NETWORK', 'Serverul nu răspunde. Verificați conexiunea și reîncercați.');
    }

    const text = await response.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        console.error(`[operator] ${method} ${path} answered non-JSON (${response.status})`, err);
      }
    }
    if (!response.ok) {
      if (data && data.error) throw new ApiError(response.status, data.reason || 'UNKNOWN', data.error);
      throw new ApiError(
        response.status,
        'UNEXPECTED',
        `Serverul a răspuns neașteptat (cod ${response.status}). Reîncercați.`
      );
    }
    if (data === null) {
      throw new ApiError(response.status, 'UNEXPECTED', 'Serverul a trimis un răspuns gol.');
    }
    return data;
  }

  const id = (n) => encodeURIComponent(String(n));
  return {
    login: (email, parola) => call('POST', '/login', { email, parola }),
    verify: (pending, cod) => call('POST', '/verifica', { pending, cod }),
    logout: () => call('POST', '/logout', {}),
    list: (stare) => call('GET', `/cereri?stare=${encodeURIComponent(stare)}`),
    detail: (n) => call('GET', `/cereri/${id(n)}`),
    plan: (n, codProgram, dbName) =>
      call('POST', `/cereri/${id(n)}/verifica`, { cod_program: codProgram, db_name: dbName }),
    approve: (n, codProgram, dbName) =>
      call('POST', `/cereri/${id(n)}/aproba`, { cod_program: codProgram, db_name: dbName }),
    job: (jobId, from) => call('GET', `/joburi/${encodeURIComponent(jobId)}?de_la=${from}`),
    reject: (n, motiv) => call('POST', `/cereri/${id(n)}/respinge`, { motiv }),
    newLink: (n) => call('POST', `/cereri/${id(n)}/link-nou`, {}),
  };
}
