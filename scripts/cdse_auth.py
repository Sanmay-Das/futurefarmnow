from cdsetool.credentials import validate_credentials

if __name__ == "__main__":
    # Obtain access token
    result = validate_credentials()
    if result:
      print("Connected established successfully")
    else:
      print("Invalid credentials")

