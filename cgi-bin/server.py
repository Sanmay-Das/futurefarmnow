from flask import Flask, send_from_directory
from soil_stats import soil_stats_bp
from soil_sample import soil_sample_bp

app = Flask(__name__)

# Register blueprints
app.register_blueprint(soil_stats_bp)
app.register_blueprint(soil_sample_bp)

if __name__ == "__main__":
    # Serve static files only in development mode
    @app.route('/public_html/<path:filename>')
    def serve_static(filename):
        static_folder = '../public_html'
        return send_from_directory(static_folder, filename)

    app.run(debug=True)
