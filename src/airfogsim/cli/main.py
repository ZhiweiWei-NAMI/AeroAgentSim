"""
AirFogSim CLI Main Module

This module provides the main entry point for the AirFogSim command-line interface.
"""

import sys
import os
import argparse
import shutil
from pathlib import Path


def export_docs(output_dir=None, format_type='markdown'):
    """
    Export AirFogSim documentation to the specified directory.

    Args:
        output_dir (str): The directory to export documentation to. Defaults to './airfogsim_docs'.
        format_type (str): Format type - 'markdown' for existing docs, 'html' for Sphinx docs.

    Returns:
        bool: True if successful, False otherwise.
    """
    if output_dir is None:
        output_dir = './airfogsim_docs'

    output_path = Path(output_dir)

    # Create output directory if it doesn't exist
    output_path.mkdir(parents=True, exist_ok=True)

    if format_type == 'html':
        return build_sphinx_docs(output_path)
    else:
        return export_markdown_docs(output_path)


def build_sphinx_docs(output_path):
    """
    Build Sphinx HTML documentation.

    Args:
        output_path (Path): Output directory for documentation.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        import subprocess

        # Find the docs directory with Sphinx configuration
        docs_dir = None
        current_dir = Path.cwd()

        # Look for docs directory with conf.py
        potential_paths = [
            current_dir / 'docs',
            current_dir.parent / 'docs',
        ]

        for path in potential_paths:
            if (path / 'conf.py').exists():
                docs_dir = path
                break

        if docs_dir is None:
            print("Error: Could not find Sphinx documentation directory (with conf.py)")
            print("Please run this command from the project root directory")
            return False

        print(f"Building Sphinx documentation from: {docs_dir}")

        # Build HTML documentation
        build_dir = output_path / 'html'
        build_dir.mkdir(parents=True, exist_ok=True)

        # Run sphinx-build
        cmd = [
            'sphinx-build',
            '-b', 'html',
            str(docs_dir),
            str(build_dir)
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            print(f"Sphinx documentation built successfully in: {build_dir}")
            print(f"Open {build_dir / 'index.html'} in your browser to view the documentation")
            return True
        else:
            print(f"Error building Sphinx documentation:")
            print(result.stderr)
            return False

    except ImportError:
        print("Error: Sphinx is not installed. Install with: pip install sphinx")
        return False
    except Exception as e:
        print(f"Error building Sphinx documentation: {str(e)}")
        return False


def export_markdown_docs(output_path):
    """
    Export existing markdown documentation.

    Args:
        output_path (Path): Output directory for documentation.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        # Try multiple methods to find the docs directory
        docs_path = None

        # Method 1: Using importlib
        try:
            import importlib.util
            spec = importlib.util.find_spec('airfogsim')
            if spec is not None and spec.origin is not None:
                package_path = Path(spec.origin).parent
                potential_docs_path = package_path / 'docs'
                if potential_docs_path.exists():
                    docs_path = potential_docs_path
        except Exception:
            pass

        # Method 2: Using direct import
        if docs_path is None:
            try:
                import airfogsim
                if hasattr(airfogsim, '__file__') and airfogsim.__file__ is not None:
                    package_path = Path(airfogsim.__file__).parent
                    potential_docs_path = package_path / 'docs'
                    if potential_docs_path.exists():
                        docs_path = potential_docs_path
            except Exception:
                pass

        # Method 3: Look in current directory structure
        if docs_path is None:
            current_dir = Path.cwd()
            potential_paths = [
                current_dir / 'src' / 'airfogsim' / 'docs',
                current_dir / 'airfogsim' / 'docs',
                current_dir / 'docs'
            ]

            for path in potential_paths:
                if path.exists():
                    docs_path = path
                    break

        # If we still couldn't find the docs directory
        if docs_path is None:
            print("Error: Could not locate documentation directory")
            print("Please run this command from the project root directory or install the package properly")
            return False

        print(f"Using documentation from: {docs_path}")
    except Exception as e:
        print(f"Error locating documentation: {str(e)}")
        return False

    try:
        # Copy all documentation files
        for lang in ['en', 'cn']:
            lang_dir = docs_path / lang
            if lang_dir.exists():
                dest_lang_dir = output_path / lang
                dest_lang_dir.mkdir(exist_ok=True)

                # Copy all markdown files
                for doc_file in lang_dir.glob('*.md'):
                    shutil.copy2(doc_file, dest_lang_dir)

                print(f"Exported {lang} documentation to {dest_lang_dir}")

        # Copy images if they exist
        img_dir = docs_path / 'img'
        if img_dir.exists():
            dest_img_dir = output_path / 'img'
            dest_img_dir.mkdir(exist_ok=True)

            for img_file in img_dir.glob('*'):
                if img_file.is_file():
                    shutil.copy2(img_file, dest_img_dir)

            print(f"Exported documentation images to {dest_img_dir}")

        # Create an index file
        create_index_file(output_path)

        print(f"\nDocumentation successfully exported to {output_path.absolute()}")
        return True

    except Exception as e:
        print(f"Error exporting documentation: {str(e)}")
        return False


def create_index_file(output_dir):
    """
    Create an index.md file in the output directory that links to all documentation files.

    Args:
        output_dir (Path): The directory where documentation was exported.
    """
    index_content = """# AirFogSim Documentation

## English Documentation

"""
    # Add links to English docs
    en_dir = output_dir / 'en'
    if en_dir.exists():
        for doc_file in sorted(en_dir.glob('*.md')):
            doc_name = doc_file.stem.replace('_', ' ').title()
            relative_path = f"en/{doc_file.name}"
            index_content += f"- [{doc_name}]({relative_path})\n"

    index_content += """
## Chinese Documentation

"""
    # Add links to Chinese docs
    cn_dir = output_dir / 'cn'
    if cn_dir.exists():
        for doc_file in sorted(cn_dir.glob('*.md')):
            doc_name = doc_file.stem.replace('_', ' ').title()
            relative_path = f"cn/{doc_file.name}"
            index_content += f"- [{doc_name}]({relative_path})\n"

    # Write the index file
    with open(output_dir / 'index.md', 'w', encoding='utf-8') as f:
        f.write(index_content)


def run_examples(example_names=None):
    """
    Run AirFogSim examples.

    Args:
        example_names (list): List of example names to run. If None, list available examples.

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        # Try multiple methods to find the examples directory
        examples_path = None

        # Method 1: Using importlib
        try:
            import importlib.util
            spec = importlib.util.find_spec('airfogsim')
            if spec is not None and spec.origin is not None:
                package_path = Path(spec.origin).parent
                potential_examples_path = package_path / 'examples'
                if potential_examples_path.exists():
                    examples_path = potential_examples_path
        except Exception:
            pass

        # Method 2: Using direct import
        if examples_path is None:
            try:
                import airfogsim
                if hasattr(airfogsim, '__file__') and airfogsim.__file__ is not None:
                    package_path = Path(airfogsim.__file__).parent
                    potential_examples_path = package_path / 'examples'
                    if potential_examples_path.exists():
                        examples_path = potential_examples_path
            except Exception:
                pass

        # Method 3: Look in current directory structure
        if examples_path is None:
            current_dir = Path.cwd()
            potential_paths = [
                current_dir / 'src' / 'airfogsim' / 'examples',
                current_dir / 'airfogsim' / 'examples',
                current_dir / 'examples'
            ]

            for path in potential_paths:
                if path.exists():
                    examples_path = path
                    break

        # If we still couldn't find the examples directory
        if examples_path is None:
            print("Error: Could not locate examples directory")
            print("Please run this command from the project root directory or install the package properly")
            return False

        print(f"Using examples from: {examples_path}")
    except Exception as e:
        print(f"Error locating examples: {str(e)}")
        return False

    # If no examples specified, list available examples
    if not example_names:
        print("Available examples:")
        for example_file in sorted(examples_path.glob('example_*.py')):
            print(f"  - {example_file.stem}")
        return True

    # Run specified examples
    for example_name in example_names:
        # Add 'example_' prefix if not already present
        if not example_name.startswith('example_'):
            example_name = f'example_{example_name}'

        # Check if example exists
        example_file = examples_path / f"{example_name}.py"
        if not example_file.exists():
            print(f"Error: Example '{example_name}' not found")
            continue

        print(f"Running example: {example_name}")
        try:
            # Change to the examples directory
            original_dir = os.getcwd()
            os.chdir(examples_path)

            # Run the example
            exec(open(example_file).read())

            # Change back to the original directory
            os.chdir(original_dir)
            print(f"Example '{example_name}' completed successfully")
        except Exception as e:
            print(f"Error running example '{example_name}': {str(e)}")

    return True


def show_class_info(class_type=None, find_params=None):
    """
    Show information about AirFogSim classes.

    Args:
        class_type (str): Type of class to show (agent, component, task, workflow, all)
        find_params (str): Parameters to find compatible classes

    Returns:
        bool: True if successful, False otherwise.
    """
    try:
        from airfogsim.helper.class_finder import main as class_finder_main

        # Prepare arguments for class_finder
        finder_args = []

        if class_type == 'agent':
            finder_args.append('--agent')
        elif class_type == 'component':
            finder_args.append('--component')
        elif class_type == 'task':
            finder_args.append('--task')
        elif class_type == 'workflow':
            finder_args.append('--workflow')
        elif class_type == 'all' or class_type is None:
            finder_args.append('--all')

        if find_params:
            if class_type == 'agent':
                finder_args.extend(['--find-agent', find_params])
            elif class_type == 'component':
                finder_args.extend(['--find-component', find_params])
            elif class_type == 'task':
                finder_args.extend(['--find-task', find_params])

        # Call the class_finder main function with the prepared arguments
        sys.argv = ['class_finder'] + finder_args
        class_finder_main()
        return True

    except Exception as e:
        print(f"Error showing class information: {str(e)}")
        return False





def parse_args(args=None):
    """
    Parse command line arguments.

    Args:
        args (list): Command line arguments. If None, sys.argv is used.

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(description='AirFogSim Command Line Interface')
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Docs command
    docs_parser = subparsers.add_parser('docs', help='Export documentation')
    docs_parser.add_argument('--output-dir', type=str, default='./airfogsim_docs',
                           help='Directory to export documentation to')
    docs_parser.add_argument('--format', choices=['markdown', 'html'], default='markdown',
                           help='Documentation format (markdown for existing docs, html for Sphinx API docs)')

    # Examples command
    examples_parser = subparsers.add_parser('examples', help='Run examples')
    examples_parser.add_argument('names', nargs='*', help='Names of examples to run')

    # Classes command
    classes_parser = subparsers.add_parser('classes', help='Show class information')
    classes_parser.add_argument('--type', choices=['agent', 'component', 'task', 'workflow', 'all'],
                              default='all', help='Type of class to show')
    # classes_parser.add_argument('--find', type=str, help='Parameters to find compatible classes (comma-separated)')

    # Parse arguments
    if args is None:
        return parser.parse_args()
    else:
        return parser.parse_args(args)


def process_args(args):
    """
    Process command line arguments.

    Args:
        args (argparse.Namespace): Parsed arguments.

    Returns:
        int: Exit code (0 for success, non-zero for failure).
    """
    # Handle special case for -docs shorthand
    if args.command == 'docs':
        output_dir = args.output_dir
        format_type = getattr(args, 'format', 'markdown')
        success = export_docs(output_dir, format_type)
        return 0 if success else 1

    # Handle examples command
    elif args.command == 'examples':
        success = run_examples(args.names)
        return 0 if success else 1

    # Handle classes command
    elif args.command == 'classes':
        success = show_class_info(args.type, args.find)
        return 0 if success else 1

    # No command specified, show help
    else:
        print("Please specify a command. Use --help for more information.")
        return 1


def process_shorthand_args(args):
    """
    Process shorthand command line arguments like -docs.

    Args:
        args (list): Command line arguments.

    Returns:
        list: Processed arguments.
    """
    if not args:
        return args

    # Handle -docs shorthand
    if args[0] == '-docs':
        new_args = ['docs']

        # Check if there's an output_dir parameter
        if len(args) > 1 and '=' in args[1]:
            param, value = args[1].split('=', 1)
            if param == 'output_dir':
                new_args.extend(['--output-dir', value])

        return new_args

    # Handle -examples shorthand
    elif args[0] == '-examples':
        new_args = ['examples']
        new_args.extend(args[1:])
        return new_args

    # Handle -classes shorthand
    elif args[0] == '-classes':
        new_args = ['classes']

        # Process additional parameters
        i = 1
        while i < len(args):
            if args[i].startswith('type='):
                new_args.extend(['--type', args[i].split('=', 1)[1]])
            elif args[i].startswith('find='):
                new_args.extend(['--find', args[i].split('=', 1)[1]])
            else:
                new_args.append(args[i])
            i += 1

        return new_args

    return args


def main():
    """
    Main entry point for the AirFogSim CLI.

    Returns:
        int: Exit code (0 for success, non-zero for failure).
    """
    # Process shorthand arguments
    processed_args = process_shorthand_args(sys.argv[1:])

    # Parse arguments
    args = parse_args(processed_args)

    # Process arguments
    return process_args(args)


if __name__ == '__main__':
    sys.exit(main())
