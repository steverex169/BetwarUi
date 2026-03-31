from waitress import serve
import app  # your Flask instance

# Run Waitress
serve(app.app, host="0.0.0.0", port=5000)
