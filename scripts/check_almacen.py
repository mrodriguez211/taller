import requests
s=requests.Session()
login={'usuario':'admin','password':'admin123','codigo':'1001'}
r=s.post('http://127.0.0.1:5000/login', data=login)
print('Login status', r.status_code)
r2=s.get('http://127.0.0.1:5000/pronto/almacen')
print('Almacen GET status', r2.status_code)
print(r2.text[:800])
