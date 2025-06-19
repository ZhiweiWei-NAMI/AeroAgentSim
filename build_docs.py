#!/usr/bin/env python3
"""
Build script for AirFogSim documentation.

This script provides a convenient way to build the Sphinx documentation
with proper dependency checking and error handling.
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path


def check_dependencies():
    """Check if required dependencies are installed."""
    required_packages = [
        'sphinx',
        'sphinx-rtd-theme',
        'sphinx-autodoc-typehints',
        'myst-parser',
        'sphinx-copybutton'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package.replace('-', '_'))
        except ImportError:
            missing_packages.append(package)
    
    if missing_packages:
        print("Missing required packages:")
        for package in missing_packages:
            print(f"  - {package}")
        print("\nInstall with: pip install -e \".[docs]\"")
        return False
    
    return True


def clean_build_dir():
    """Clean the build directory."""
    build_dir = Path('docs/_build')
    if build_dir.exists():
        print("Cleaning build directory...")
        shutil.rmtree(build_dir)


def build_html_docs():
    """Build HTML documentation."""
    print("Building HTML documentation...")
    
    cmd = [
        'sphinx-build',
        '-b', 'html',
        '-W',  # Treat warnings as errors
        'docs',
        'docs/_build/html'
    ]
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("HTML documentation built successfully!")
        print(f"Output: {Path('docs/_build/html/index.html').absolute()}")
        return True
    except subprocess.CalledProcessError as e:
        print("Error building HTML documentation:")
        print(e.stderr)
        return False


def build_pdf_docs():
    """Build PDF documentation (requires LaTeX)."""
    print("Building PDF documentation...")
    
    cmd = [
        'sphinx-build',
        '-b', 'latex',
        'docs',
        'docs/_build/latex'
    ]
    
    try:
        # Build LaTeX files
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        
        # Build PDF from LaTeX
        latex_dir = Path('docs/_build/latex')
        pdf_cmd = ['make', 'all-pdf']
        
        result = subprocess.run(pdf_cmd, cwd=latex_dir, check=True, 
                              capture_output=True, text=True)
        
        print("PDF documentation built successfully!")
        pdf_file = latex_dir / 'AirFogSim.pdf'
        if pdf_file.exists():
            print(f"Output: {pdf_file.absolute()}")
        return True
        
    except subprocess.CalledProcessError as e:
        print("Error building PDF documentation:")
        print("Note: PDF generation requires LaTeX to be installed")
        print(e.stderr)
        return False


def serve_docs(port=8000):
    """Serve documentation locally."""
    html_dir = Path('docs/_build/html')
    
    if not html_dir.exists():
        print("HTML documentation not found. Building first...")
        if not build_html_docs():
            return False
    
    print(f"Serving documentation at http://localhost:{port}")
    print("Press Ctrl+C to stop the server")
    
    try:
        import http.server
        import socketserver
        
        os.chdir(html_dir)
        
        with socketserver.TCPServer(("", port), http.server.SimpleHTTPRequestHandler) as httpd:
            httpd.serve_forever()
            
    except KeyboardInterrupt:
        print("\nServer stopped")
        return True
    except Exception as e:
        print(f"Error serving documentation: {e}")
        return False


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Build AirFogSim documentation')
    parser.add_argument('--clean', action='store_true', 
                       help='Clean build directory before building')
    parser.add_argument('--html', action='store_true', default=True,
                       help='Build HTML documentation (default)')
    parser.add_argument('--pdf', action='store_true',
                       help='Build PDF documentation (requires LaTeX)')
    parser.add_argument('--serve', action='store_true',
                       help='Serve documentation locally')
    parser.add_argument('--port', type=int, default=8000,
                       help='Port for local server (default: 8000)')
    parser.add_argument('--check-deps', action='store_true',
                       help='Check documentation dependencies')
    
    args = parser.parse_args()
    
    # Change to project root directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Check dependencies
    if args.check_deps or not check_dependencies():
        if not check_dependencies():
            return 1
        if args.check_deps:
            print("All dependencies are installed!")
            return 0
    
    # Clean build directory if requested
    if args.clean:
        clean_build_dir()
    
    success = True
    
    # Build HTML documentation
    if args.html and not args.pdf and not args.serve:
        success = build_html_docs()
    
    # Build PDF documentation
    if args.pdf:
        success = build_pdf_docs() and success
    
    # Serve documentation
    if args.serve:
        success = serve_docs(args.port) and success
    
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
