"""Print a scrypt hash for EDITOR_PASSWORD_HASH; never echo the password."""
from getpass import getpass
from werkzeug.security import generate_password_hash

if __name__ == '__main__':
    password = getpass('New website-editor password (12–128 characters): ')
    if not 12 <= len(password) <= 128:
        raise SystemExit('Use 12–128 characters.')
    if password != getpass('Confirm password: '):
        raise SystemExit('Passwords do not match.')
    print(generate_password_hash(password))
