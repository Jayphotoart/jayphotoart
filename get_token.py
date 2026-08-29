import pickle

# token.pickle ફાઈલમાંથી ડેટા વાંચવો
with open('token.pickle', 'rb') as token_file:
    creds = pickle.load(token_file)

print("--- આ માહિતી કોપી કરી લો ---")
print("client_id =", creds.client_id)
print("client_secret =", creds.client_secret)
print("refresh_token =", creds.refresh_token)