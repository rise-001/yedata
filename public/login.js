const form = document.querySelector('#loginForm');
const username = document.querySelector('#adminUsername');
const password = document.querySelector('#adminPassword');
const error = document.querySelector('#loginError');
const submit = document.querySelector('#loginSubmit');

async function redirectExistingSession() {
  try {
    const response = await fetch('/api/session');
    const result = await response.json();
    if (result.ok) location.replace('/');
  } catch {
    error.textContent = '暂时无法连接服务器';
  }
}

if (new URLSearchParams(location.search).get('reason') === 'expired') {
  error.textContent = '登录已过期，请重新登录';
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  error.textContent = '';
  submit.disabled = true;
  try {
    const response = await fetch('/api/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ username: username.value.trim(), password: password.value })
    });
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.message || '登录失败');
    location.replace('/');
  } catch (loginError) {
    error.textContent = loginError.message || '登录失败';
    password.select();
  } finally {
    submit.disabled = false;
  }
});

redirectExistingSession();
