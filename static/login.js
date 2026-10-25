const form = document.getElementById('login-form');
form.onsubmit = async event => {
  event.preventDefault();
  const button = document.getElementById('login-button'), error = document.getElementById('login-error');
  button.disabled = true; error.textContent = '';
  const password = document.getElementById('password');
  try {
    const response = await fetch('/api/login', {method:'POST', cache:'no-store',
      headers:{'Content-Type':'application/json','X-Requested-With':'AirTagMap'},
      body:JSON.stringify({username:document.getElementById('username').value, password:password.value})});
    password.value = '';
    const value = await response.json();
    if (!response.ok) throw Error(value.error || 'Inloggningen misslyckades');
    window.location.replace('/');
  } catch (e) { error.textContent = e.message; }
  finally { password.value = ''; button.disabled = false; }
};
