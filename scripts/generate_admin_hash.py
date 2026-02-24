from getpass import getpass

from flask_bcrypt import Bcrypt


def main():
    bcrypt = Bcrypt()
    password = getpass("Enter admin password to hash: ")
    hashed = bcrypt.generate_password_hash(password).decode("utf-8")

    print("\nUse this SQL:\n")
    print("INSERT INTO admin (email, password)")
    print(f"VALUES ('admin@blood.com', '{hashed}');")


if __name__ == "__main__":
    main()
