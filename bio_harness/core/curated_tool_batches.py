from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from bio_harness.core.tool_onboarding import install_tool_onboarding_batch


CURATED_TOOL_BATCHES: list[dict[str, Any]] = [
    {
        "id": "expression_core",
        "title": "Expression Core",
        "description": "Differential expression + quantification foundations.",
        "priority": 1,
        "tools": [
            {
                "draft": {
                    "skill_name": "deseq2_run",
                    "description": "Run DESeq2 differential expression from count matrix + sample metadata.",
                    "risk_level": "medium",
                    "tools_required": ["rscript", "deseq2"],
                    "capabilities": ["differential_analysis", "group_comparison"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 2},
                    "command_template": (
                        "Rscript {script_path} --counts {counts_matrix} --metadata {metadata_table} "
                        "--design {design_formula} --contrast {contrast} --outdir {output_dir}"
                    ),
                    "parameters": {
                        "script_path": {"type": "path", "description": "Path to DESeq2 wrapper R script.", "required": True},
                        "counts_matrix": {"type": "path", "description": "Gene/sample counts TSV matrix.", "required": True},
                        "metadata_table": {"type": "path", "description": "Sample metadata table.", "required": True},
                        "design_formula": {"type": "string", "description": "Design formula (e.g. ~ condition).", "required": True},
                        "contrast": {"type": "string", "description": "Contrast tuple/list label.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory for DE tables.", "required": True},
                    },
                    "usage_guide": "Use for count-based RNA-seq differential expression with explicit group contrasts.",
                },
                "source_meta": {"source": "https://bioconductor.org/packages/DESeq2/", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "edger_run",
                    "description": "Run edgeR GLM differential expression from counts and metadata.",
                    "risk_level": "medium",
                    "tools_required": ["rscript", "edger"],
                    "capabilities": ["differential_analysis", "group_comparison"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 2},
                    "command_template": (
                        "Rscript {script_path} --counts {counts_matrix} --metadata {metadata_table} "
                        "--design {design_formula} --contrast {contrast} --outdir {output_dir}"
                    ),
                    "parameters": {
                        "script_path": {"type": "path", "description": "Path to edgeR wrapper R script.", "required": True},
                        "counts_matrix": {"type": "path", "description": "Raw count matrix file.", "required": True},
                        "metadata_table": {"type": "path", "description": "Sample metadata table.", "required": True},
                        "design_formula": {"type": "string", "description": "GLM design formula.", "required": True},
                        "contrast": {"type": "string", "description": "edgeR contrast specification.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory for result tables.", "required": True},
                    },
                    "usage_guide": "Use for negative-binomial differential expression workflows with edgeR.",
                },
                "source_meta": {"source": "https://bioconductor.org/packages/edgeR/", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "limma_voom_run",
                    "description": "Run limma-voom differential expression analysis from counts and metadata.",
                    "risk_level": "medium",
                    "tools_required": ["rscript", "limma"],
                    "capabilities": ["differential_analysis", "group_comparison"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 2},
                    "command_template": (
                        "Rscript {script_path} --counts {counts_matrix} --metadata {metadata_table} "
                        "--design {design_formula} --contrast {contrast} --outdir {output_dir}"
                    ),
                    "parameters": {
                        "script_path": {"type": "path", "description": "Path to limma-voom wrapper script.", "required": True},
                        "counts_matrix": {"type": "path", "description": "Count matrix input.", "required": True},
                        "metadata_table": {"type": "path", "description": "Sample annotation table.", "required": True},
                        "design_formula": {"type": "string", "description": "limma design formula.", "required": True},
                        "contrast": {"type": "string", "description": "Target contrast.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory.", "required": True},
                    },
                    "usage_guide": "Use for voom-normalized linear-model differential analysis in bulk RNA-seq.",
                },
                "source_meta": {"source": "https://bioconductor.org/packages/limma/", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "featurecounts_run",
                    "description": "Quantify aligned reads to genes/exons using featureCounts.",
                    "risk_level": "medium",
                    "tools_required": ["featurecounts", "subread"],
                    "capabilities": ["quantification"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 4},
                    "command_template": "featureCounts -T {threads} -a {annotation_gtf} -o {output_counts} {input_bams}",
                    "parameters": {
                        "threads": {"type": "integer", "description": "Number of worker threads.", "required": True},
                        "annotation_gtf": {"type": "path", "description": "Reference GTF annotation.", "required": True},
                        "output_counts": {"type": "path", "description": "Output counts file path.", "required": True},
                        "input_bams": {"type": "string", "description": "Space-separated BAM inputs.", "required": True},
                    },
                    "usage_guide": "Use for gene-level read counting from coordinate-sorted BAM files.",
                },
                "source_meta": {"source": "https://subread.sourceforge.net/featureCounts.html", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "salmon_quant",
                    "description": "Quantify transcript abundance with Salmon quasi-mapping.",
                    "risk_level": "medium",
                    "tools_required": ["salmon"],
                    "capabilities": ["quantification"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 4},
                    "command_template": (
                        "salmon quant -i {index_dir} -l {library_type} -1 {reads_1} -2 {reads_2} "
                        "--validateMappings -p {threads} -o {output_dir}"
                    ),
                    "parameters": {
                        "index_dir": {"type": "path", "description": "Salmon transcriptome index directory.", "required": True},
                        "library_type": {"type": "string", "description": "Library type string (e.g. A, ISR).", "required": True},
                        "reads_1": {"type": "path", "description": "Read 1 FASTQ(.gz).", "required": True},
                        "reads_2": {"type": "path", "description": "Read 2 FASTQ(.gz).", "required": True},
                        "threads": {"type": "integer", "description": "Thread count.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory.", "required": True},
                    },
                    "usage_guide": "Use for fast transcript-level quantification in bulk RNA-seq pipelines.",
                },
                "source_meta": {"source": "https://salmon.readthedocs.io/en/latest/salmon.html", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "kallisto_quant",
                    "description": "Quantify transcript abundance with kallisto pseudoalignment.",
                    "risk_level": "medium",
                    "tools_required": ["kallisto"],
                    "capabilities": ["quantification"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 4},
                    "command_template": (
                        "kallisto quant -i {index_path} -o {output_dir} -t {threads} {reads_1} {reads_2}"
                    ),
                    "parameters": {
                        "index_path": {"type": "path", "description": "kallisto index file.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory.", "required": True},
                        "threads": {"type": "integer", "description": "Thread count.", "required": True},
                        "reads_1": {"type": "path", "description": "Read 1 FASTQ(.gz).", "required": True},
                        "reads_2": {"type": "path", "description": "Read 2 FASTQ(.gz).", "required": True},
                    },
                    "usage_guide": "Use for lightweight transcript quantification with bootstrap options added via command override.",
                },
                "source_meta": {"source": "https://pachterlab.github.io/kallisto/", "mode": "official_docs"},
            },
        ],
    },
    {
        "id": "alignment_variant_core",
        "title": "Alignment + Variant Core",
        "description": "Common aligners plus germline variant calling stack.",
        "priority": 2,
        "tools": [
            {
                "draft": {
                    "skill_name": "star_align",
                    "description": "Align RNA-seq reads to reference genome using STAR.",
                    "risk_level": "medium",
                    "tools_required": ["star"],
                    "capabilities": ["alignment", "reference_inputs"],
                    "system_requirements": {"min_ram_gb": 24, "min_cores": 8},
                    "command_template": (
                        "STAR --runThreadN {threads} --genomeDir {genome_dir} "
                        "--readFilesIn {reads_1} {reads_2} --readFilesCommand zcat "
                        "--outFileNamePrefix {output_prefix}"
                    ),
                    "parameters": {
                        "threads": {"type": "integer", "description": "Thread count.", "required": True},
                        "genome_dir": {"type": "path", "description": "STAR genome index directory.", "required": True},
                        "reads_1": {"type": "path", "description": "Read 1 FASTQ(.gz).", "required": True},
                        "reads_2": {"type": "path", "description": "Read 2 FASTQ(.gz).", "required": True},
                        "output_prefix": {"type": "path", "description": "Output prefix path.", "required": True},
                    },
                    "usage_guide": "Use for splice-aware short-read alignment in RNA-seq.",
                },
                "source_meta": {"source": "https://github.com/alexdobin/STAR", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "star_2pass_align",
                    "description": "Run STAR two-pass alignment mode for improved novel junction detection.",
                    "risk_level": "medium",
                    "tools_required": ["star"],
                    "capabilities": ["alignment", "reference_inputs"],
                    "system_requirements": {"min_ram_gb": 32, "min_cores": 8},
                    "command_template": (
                        "STAR --runMode alignReads --twopassMode Basic --runThreadN {threads} "
                        "--genomeDir {genome_dir} --readFilesIn {reads_1} {reads_2} "
                        "--readFilesCommand zcat --outFileNamePrefix {output_prefix}"
                    ),
                    "parameters": {
                        "threads": {"type": "integer", "description": "Thread count.", "required": True},
                        "genome_dir": {"type": "path", "description": "STAR genome index directory.", "required": True},
                        "reads_1": {"type": "path", "description": "Read 1 FASTQ(.gz).", "required": True},
                        "reads_2": {"type": "path", "description": "Read 2 FASTQ(.gz).", "required": True},
                        "output_prefix": {"type": "path", "description": "Output prefix path.", "required": True},
                    },
                    "usage_guide": "Use when two-pass junction discovery is required before quantification/splicing analysis.",
                },
                "source_meta": {"source": "https://github.com/alexdobin/STAR", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "hisat2_align",
                    "description": "Align reads to genome/transcriptome using HISAT2.",
                    "risk_level": "medium",
                    "tools_required": ["hisat2"],
                    "capabilities": ["alignment", "reference_inputs"],
                    "system_requirements": {"min_ram_gb": 16, "min_cores": 8},
                    "command_template": (
                        "hisat2 -x {index_base} -1 {reads_1} -2 {reads_2} -p {threads} -S {output_sam}"
                    ),
                    "parameters": {
                        "index_base": {"type": "path", "description": "HISAT2 index basename.", "required": True},
                        "reads_1": {"type": "path", "description": "Read 1 FASTQ(.gz).", "required": True},
                        "reads_2": {"type": "path", "description": "Read 2 FASTQ(.gz).", "required": True},
                        "threads": {"type": "integer", "description": "Thread count.", "required": True},
                        "output_sam": {"type": "path", "description": "Output SAM path.", "required": True},
                    },
                    "usage_guide": "Use for memory-efficient splice-aware alignment.",
                },
                "source_meta": {"source": "https://daehwankimlab.github.io/hisat2/", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "gatk_haplotypecaller",
                    "description": "Call germline SNPs/indels with GATK HaplotypeCaller.",
                    "risk_level": "high",
                    "tools_required": ["gatk"],
                    "capabilities": ["variant_calling", "reference_inputs"],
                    "system_requirements": {"min_ram_gb": 16, "min_cores": 4},
                    "command_template": (
                        "gatk HaplotypeCaller -R {reference_fasta} -I {input_bam} -O {output_vcf}"
                    ),
                    "parameters": {
                        "reference_fasta": {"type": "path", "description": "Reference FASTA.", "required": True},
                        "input_bam": {"type": "path", "description": "Deduplicated BAM input.", "required": True},
                        "output_vcf": {"type": "path", "description": "Output VCF or gVCF path.", "required": True},
                    },
                    "usage_guide": "Use for Broad-style germline variant calling with proper preprocessing upstream.",
                },
                "source_meta": {
                    "source": "https://gatk.broadinstitute.org/hc/en-us/articles/360037225632-HaplotypeCaller",
                    "mode": "official_docs",
                },
            },
            {
                "draft": {
                    "skill_name": "bcftools_call",
                    "description": "Call variants via bcftools mpileup + call pipeline.",
                    "risk_level": "medium",
                    "tools_required": ["bcftools", "samtools"],
                    "capabilities": ["variant_calling", "reference_inputs"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 4},
                    "command_template": (
                        "bcftools mpileup -f {reference_fasta} {input_bam} | "
                        "bcftools call -mv -Oz -o {output_vcf_gz}"
                    ),
                    "parameters": {
                        "reference_fasta": {"type": "path", "description": "Reference FASTA path.", "required": True},
                        "input_bam": {"type": "path", "description": "Input BAM/CRAM file.", "required": True},
                        "output_vcf_gz": {"type": "path", "description": "Compressed output VCF path.", "required": True},
                    },
                    "usage_guide": "Use for lightweight germline variant calling in WGS/WES pipelines.",
                },
                "source_meta": {"source": "https://samtools.github.io/bcftools/bcftools.html", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "freebayes_call",
                    "description": "Call variants with haplotype-based FreeBayes.",
                    "risk_level": "medium",
                    "tools_required": ["freebayes"],
                    "capabilities": ["variant_calling", "reference_inputs"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 4},
                    "command_template": "freebayes -f {reference_fasta} {input_bam} > {output_vcf}",
                    "parameters": {
                        "reference_fasta": {"type": "path", "description": "Reference FASTA path.", "required": True},
                        "input_bam": {"type": "path", "description": "Input BAM file.", "required": True},
                        "output_vcf": {"type": "path", "description": "Output VCF path.", "required": True},
                    },
                    "usage_guide": "Use for haplotype-based variant calling, including pooled/complex settings via command override.",
                },
                "source_meta": {"source": "https://github.com/freebayes/freebayes", "mode": "official_docs"},
            },
        ],
    },
    {
        "id": "single_cell_core",
        "title": "Single-Cell Core",
        "description": "scanpy, Seurat-compatible scripting, Cell Ranger, and STARsolo.",
        "priority": 3,
        "tools": [
            {
                "draft": {
                    "skill_name": "scanpy_workflow",
                    "description": "Run a scanpy preprocessing/clustering workflow script.",
                    "risk_level": "medium",
                    "tools_required": ["python", "scanpy"],
                    "capabilities": ["single_cell_analysis"],
                    "system_requirements": {"min_ram_gb": 16, "min_cores": 4},
                    "command_template": "python {script_path} --input-h5ad {input_h5ad} --output-dir {output_dir}",
                    "parameters": {
                        "script_path": {"type": "path", "description": "Path to scanpy workflow script.", "required": True},
                        "input_h5ad": {"type": "path", "description": "Input AnnData h5ad file.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory for plots/tables.", "required": True},
                    },
                    "usage_guide": "Use for reproducible scanpy workflows encapsulated as explicit scripts.",
                },
                "source_meta": {"source": "https://scanpy.readthedocs.io/en/stable/", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "seurat_rscript_workflow",
                    "description": "Run Seurat workflow from an Rscript wrapper for CLI orchestration.",
                    "risk_level": "medium",
                    "tools_required": ["rscript", "seurat"],
                    "capabilities": ["single_cell_analysis"],
                    "system_requirements": {"min_ram_gb": 16, "min_cores": 4},
                    "command_template": (
                        "Rscript {script_path} --matrix {input_matrix} --metadata {metadata_table} --output-dir {output_dir}"
                    ),
                    "parameters": {
                        "script_path": {"type": "path", "description": "Path to Seurat wrapper script.", "required": True},
                        "input_matrix": {"type": "path", "description": "Input matrix path (e.g. MTX/H5).", "required": True},
                        "metadata_table": {"type": "path", "description": "Cell metadata table.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory.", "required": True},
                    },
                    "usage_guide": "Use for Seurat analyses integrated into non-interactive pipeline runs.",
                },
                "source_meta": {"source": "https://satijalab.org/seurat/", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "cellranger_count",
                    "description": "Run 10x Cell Ranger gene-expression counting pipeline.",
                    "risk_level": "high",
                    "tools_required": ["cellranger"],
                    "capabilities": ["single_cell_analysis", "reference_inputs"],
                    "system_requirements": {"min_ram_gb": 32, "min_cores": 8},
                    "command_template": (
                        "cellranger count --id={run_id} --transcriptome={transcriptome_ref} "
                        "--fastqs={fastq_dir} --sample={sample_name}"
                    ),
                    "parameters": {
                        "run_id": {"type": "string", "description": "Cell Ranger run identifier.", "required": True},
                        "transcriptome_ref": {"type": "path", "description": "Cell Ranger reference package directory.", "required": True},
                        "fastq_dir": {"type": "path", "description": "Directory containing FASTQ files.", "required": True},
                        "sample_name": {"type": "string", "description": "Sample prefix in FASTQ names.", "required": True},
                    },
                    "usage_guide": "Use for 10x single-cell GEX processing under validated reference bundles.",
                },
                "source_meta": {
                    "source": "https://www.10xgenomics.com/support/software/cell-ranger/latest/analysis/running-pipelines/cr-gex-count",
                    "mode": "official_docs",
                },
            },
            {
                "draft": {
                    "skill_name": "star_solo_count",
                    "description": "Run STARsolo for single-cell alignment and UMI counting.",
                    "risk_level": "high",
                    "tools_required": ["star"],
                    "capabilities": ["single_cell_analysis", "alignment", "reference_inputs"],
                    "system_requirements": {"min_ram_gb": 32, "min_cores": 8},
                    "command_template": (
                        "STAR --runThreadN {threads} --genomeDir {genome_dir} "
                        "--readFilesIn {reads_2} {reads_1} --readFilesCommand zcat "
                        "--soloType CB_UMI_Simple --soloCBwhitelist {whitelist} "
                        "--soloFeatures Gene --outFileNamePrefix {output_prefix}"
                    ),
                    "parameters": {
                        "threads": {"type": "integer", "description": "Thread count.", "required": True},
                        "genome_dir": {"type": "path", "description": "STAR genome index directory.", "required": True},
                        "reads_1": {"type": "path", "description": "Read 1 FASTQ(.gz).", "required": True},
                        "reads_2": {"type": "path", "description": "Read 2 FASTQ(.gz).", "required": True},
                        "whitelist": {"type": "path", "description": "Cell barcode whitelist.", "required": True},
                        "output_prefix": {"type": "path", "description": "Output prefix path.", "required": True},
                    },
                    "usage_guide": "Use for STAR-native single-cell counting when Cell Ranger is not required.",
                },
                "source_meta": {"source": "https://github.com/alexdobin/STAR", "mode": "official_docs"},
            },
        ],
    },
    {
        "id": "annotation_assembly_core",
        "title": "Annotation + Assembly Core",
        "description": "Common annotation and de novo assembly tools.",
        "priority": 4,
        "tools": [
            {
                "draft": {
                    "skill_name": "snpeff_annotate",
                    "description": "Annotate variants with predicted functional impact via SnpEff.",
                    "risk_level": "medium",
                    "tools_required": ["snpeff"],
                    "capabilities": ["annotation", "variant_calling"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 2},
                    "command_template": "snpEff {genome_db} {input_vcf} > {output_vcf}",
                    "parameters": {
                        "genome_db": {"type": "string", "description": "SnpEff genome database key.", "required": True},
                        "input_vcf": {"type": "path", "description": "Input VCF file.", "required": True},
                        "output_vcf": {"type": "path", "description": "Output annotated VCF path.", "required": True},
                    },
                    "usage_guide": "Use for variant consequence annotation after calling/filtering.",
                },
                "source_meta": {"source": "https://pcingola.github.io/SnpEff/", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "vep_annotate",
                    "description": "Annotate variants with Ensembl VEP in offline cache mode.",
                    "risk_level": "medium",
                    "tools_required": ["vep"],
                    "capabilities": ["annotation", "variant_calling"],
                    "system_requirements": {"min_ram_gb": 12, "min_cores": 4},
                    "command_template": (
                        "vep --cache --offline --assembly {assembly} -i {input_vcf} -o {output_vcf} --vcf"
                    ),
                    "parameters": {
                        "assembly": {"type": "string", "description": "Genome assembly name (e.g. GRCh38).", "required": True},
                        "input_vcf": {"type": "path", "description": "Input VCF file.", "required": True},
                        "output_vcf": {"type": "path", "description": "Output VCF path.", "required": True},
                    },
                    "usage_guide": "Use for Ensembl consequence annotation with local caches for reproducibility.",
                },
                "source_meta": {"source": "https://www.ensembl.org/info/docs/tools/vep/index.html", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "prokka_annotate",
                    "description": "Annotate prokaryotic assemblies with Prokka.",
                    "risk_level": "medium",
                    "tools_required": ["prokka"],
                    "capabilities": ["annotation", "genome_assembly"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 4},
                    "command_template": "prokka --outdir {output_dir} --prefix {sample_prefix} {input_fasta}",
                    "parameters": {
                        "output_dir": {"type": "path", "description": "Output directory.", "required": True},
                        "sample_prefix": {"type": "string", "description": "Output file prefix.", "required": True},
                        "input_fasta": {"type": "path", "description": "Assembly FASTA input.", "required": True},
                    },
                    "usage_guide": "Use for bacterial/archaeal structural + functional annotation.",
                },
                "source_meta": {"source": "https://github.com/tseemann/prokka", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "spades_assemble",
                    "description": "Assemble short-read genomes/transcriptomes with SPAdes.",
                    "risk_level": "high",
                    "tools_required": ["spades.py"],
                    "capabilities": ["genome_assembly"],
                    "system_requirements": {"min_ram_gb": 32, "min_cores": 8},
                    "command_template": (
                        "spades.py -1 {reads_1} -2 {reads_2} -t {threads} -m {memory_gb} -o {output_dir}"
                    ),
                    "parameters": {
                        "reads_1": {"type": "path", "description": "Read 1 FASTQ(.gz).", "required": True},
                        "reads_2": {"type": "path", "description": "Read 2 FASTQ(.gz).", "required": True},
                        "threads": {"type": "integer", "description": "Thread count.", "required": True},
                        "memory_gb": {"type": "integer", "description": "Memory cap in GB.", "required": True},
                        "output_dir": {"type": "path", "description": "Assembly output directory.", "required": True},
                    },
                    "usage_guide": "Use for short-read assembly with explicit memory/thread controls.",
                },
                "source_meta": {"source": "https://github.com/ablab/spades", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "flye_assemble",
                    "description": "Assemble long reads with Flye.",
                    "risk_level": "high",
                    "tools_required": ["flye"],
                    "capabilities": ["genome_assembly"],
                    "system_requirements": {"min_ram_gb": 32, "min_cores": 8},
                    "command_template": (
                        "flye --nano-raw {reads_fastq} --threads {threads} --out-dir {output_dir} --genome-size {genome_size}"
                    ),
                    "parameters": {
                        "reads_fastq": {"type": "path", "description": "Long-read FASTQ input.", "required": True},
                        "threads": {"type": "integer", "description": "Thread count.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory.", "required": True},
                        "genome_size": {"type": "string", "description": "Estimated genome size (e.g. 5m, 3g).", "required": True},
                    },
                    "usage_guide": "Use for nanopore long-read assembly with coarse genome size estimate.",
                },
                "source_meta": {"source": "https://github.com/mikolmogorov/Flye", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "trinity_assemble",
                    "description": "Assemble transcriptomes de novo with Trinity.",
                    "risk_level": "high",
                    "tools_required": ["trinity"],
                    "capabilities": ["genome_assembly"],
                    "system_requirements": {"min_ram_gb": 32, "min_cores": 8},
                    "command_template": (
                        "Trinity --seqType fq --left {reads_1} --right {reads_2} --CPU {threads} "
                        "--max_memory {max_memory_gb}G --output {output_dir}"
                    ),
                    "parameters": {
                        "reads_1": {"type": "path", "description": "Read 1 FASTQ(.gz).", "required": True},
                        "reads_2": {"type": "path", "description": "Read 2 FASTQ(.gz).", "required": True},
                        "threads": {"type": "integer", "description": "Thread count.", "required": True},
                        "max_memory_gb": {"type": "integer", "description": "Max memory allocation in GB.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory.", "required": True},
                    },
                    "usage_guide": "Use for RNA-seq transcriptome reconstruction when no trusted reference exists.",
                },
                "source_meta": {
                    "source": "https://github.com/trinityrnaseq/trinityrnaseq/wiki",
                    "mode": "official_docs",
                },
            },
        ],
    },
    {
        "id": "chromatin_core",
        "title": "Chromatin Core",
        "description": "Peak calling support for ChIP-seq and ATAC-seq.",
        "priority": 5,
        "tools": [
            {
                "draft": {
                    "skill_name": "macs2_chipseq_callpeak",
                    "description": "Call ChIP-seq peaks with MACS2/3 callpeak mode.",
                    "risk_level": "medium",
                    "tools_required": ["macs2"],
                    "capabilities": ["chipseq_analysis"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 2},
                    "command_template": (
                        "macs2 callpeak -t {treatment_bam} -c {control_bam} -f BAM "
                        "-g {genome_size} -n {name} --outdir {output_dir}"
                    ),
                    "parameters": {
                        "treatment_bam": {"type": "path", "description": "Treatment BAM file.", "required": True},
                        "control_bam": {"type": "path", "description": "Matched control/input BAM file.", "required": True},
                        "genome_size": {"type": "string", "description": "Effective genome size (hs, mm, etc.).", "required": True},
                        "name": {"type": "string", "description": "Output sample label.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory.", "required": True},
                    },
                    "usage_guide": "Use for TF/histone ChIP-seq peak calling with treatment/control BAMs.",
                },
                "source_meta": {"source": "https://macs3-project.github.io/MACS/docs/callpeak.html", "mode": "official_docs"},
            },
            {
                "draft": {
                    "skill_name": "macs2_atacseq_callpeak",
                    "description": "Call ATAC-seq accessibility peaks with MACS2/3 tuned flags.",
                    "risk_level": "medium",
                    "tools_required": ["macs2"],
                    "capabilities": ["atacseq_analysis"],
                    "system_requirements": {"min_ram_gb": 8, "min_cores": 2},
                    "command_template": (
                        "macs2 callpeak -t {treatment_bam} -f BAMPE -g {genome_size} -n {name} "
                        "--outdir {output_dir} --nomodel --shift -100 --extsize 200"
                    ),
                    "parameters": {
                        "treatment_bam": {"type": "path", "description": "ATAC paired-end BAM file.", "required": True},
                        "genome_size": {"type": "string", "description": "Effective genome size (hs, mm, etc.).", "required": True},
                        "name": {"type": "string", "description": "Output sample label.", "required": True},
                        "output_dir": {"type": "path", "description": "Output directory.", "required": True},
                    },
                    "usage_guide": "Use for ATAC-seq peak calling with paired-end fragment mode.",
                },
                "source_meta": {"source": "https://macs3-project.github.io/MACS/docs/callpeak.html", "mode": "official_docs"},
            },
        ],
    },
]


def curated_batch_index() -> dict[str, dict[str, Any]]:
    return {str(batch.get("id", "")): batch for batch in CURATED_TOOL_BATCHES if str(batch.get("id", "")).strip()}


def iter_curated_tools() -> Iterable[dict[str, Any]]:
    for batch in CURATED_TOOL_BATCHES:
        for tool in batch.get("tools", []) if isinstance(batch.get("tools", []), list) else []:
            if isinstance(tool, dict):
                yield tool


def install_curated_batch(
    batch_id: str,
    *,
    skills_definitions_dir: Path,
    skills_library_dir: Path,
    capability_catalog_path: Path,
    record_custom_tool: bool = True,
    installed_at: str = "curated_seed_v1",
) -> dict[str, Any]:
    lookup = curated_batch_index()
    target = lookup.get(str(batch_id).strip())
    if target is None:
        raise ValueError(f"Unknown curated batch id: {batch_id}")
    tools = target.get("tools", []) if isinstance(target.get("tools", []), list) else []
    return install_tool_onboarding_batch(
        tools,
        skills_definitions_dir=skills_definitions_dir,
        skills_library_dir=skills_library_dir,
        capability_catalog_path=capability_catalog_path,
        install_workflow=f"controlled_curated_batch_onboarding:{batch_id}",
        record_custom_tool=record_custom_tool,
        installed_at=installed_at,
    )
