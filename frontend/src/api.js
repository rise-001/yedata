export async function api(path, options = {}, allowFalse = false) {
  const response = await fetch(`/api/${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  if (response.status === 401) {
    window.dispatchEvent(new Event('session-expired'));
    throw new Error('登录已过期，请重新登录');
  }
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error('服务器响应异常，请稍后重试');
  }
  if (!response.ok || (!allowFalse && !result.ok)) {
    throw new Error(result.message || '请求失败，请稍后重试');
  }
  return result;
}

export function send(path, method, body) {
  return api(path, { method, body: JSON.stringify(body) });
}
