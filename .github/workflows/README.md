# GitHub Actions Workflows

This directory contains GitHub Actions workflows for automating various tasks in the AirFogSim project.

## PDF Generation Workflow

The `generate-pdf.yml` workflow automatically generates a PDF from the `paper.md` file using the Open Journals PDF Generator.

### Features

- Automatically runs when changes are made to `paper.md`, `paper.bib`, or any files in the `figures/` directory
- Uses the [Open Journals PDF Generator](https://github.com/marketplace/actions/open-journals-pdf-generator) to compile the paper
- Follows the JOSS (Journal of Open Source Software) formatting guidelines
- Saves the generated PDF as an artifact that can be downloaded from the GitHub Actions page

### Manual Triggering

You can manually trigger the workflow by:

1. Going to the "Actions" tab in the GitHub repository
2. Selecting the "Generate PDF" workflow
3. Clicking "Run workflow"
4. Selecting the branch you want to run the workflow on
5. Clicking "Run workflow"

### Accessing the Generated PDF

After the workflow runs successfully:

1. Go to the "Actions" tab in the GitHub repository
2. Click on the completed workflow run
3. Scroll down to the "Artifacts" section
4. Click on "paper" to download the generated PDF

### Customization

If you need to customize the PDF generation process, you can modify the `generate-pdf.yml` file:

- To change which files trigger the workflow, edit the `paths` section
- To use a different PDF engine or template, modify the parameters in the `uses: openjournals/openjournals-draft-action@master` step
- To change the retention period for artifacts, modify the `retention-days` parameter (default is 30 days)
