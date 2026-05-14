#!/usr/bin/env Rscript

args <- commandArgs(trailingOnly = TRUE)

parse_args <- function(tokens) {
  out <- list()
  i <- 1
  while (i <= length(tokens)) {
    key <- tokens[[i]]
    if (!startsWith(key, "--")) {
      i <- i + 1
      next
    }
    name <- substring(key, 3)
    if (i == length(tokens) || startsWith(tokens[[i + 1]], "--")) {
      out[[name]] <- TRUE
      i <- i + 1
    } else {
      out[[name]] <- tokens[[i + 1]]
      i <- i + 2
    }
  }
  out
}

parse_contrast <- function(raw) {
  parts <- strsplit(as.character(raw), "_", fixed = TRUE)[[1]]
  if (length(parts) >= 4 && tolower(parts[[3]]) == "vs") {
    return(list(
      factor_name = parts[[1]],
      treatment = parts[[2]],
      control = parts[[4]]
    ))
  }
  list(factor_name = "condition", treatment = "", control = "")
}

normalize_sample <- function(x) basename(as.character(x))

match_metadata_idx <- function(sample_name, meta_names) {
  sample_norm <- tolower(normalize_sample(sample_name))
  meta_norm <- tolower(normalize_sample(meta_names))
  idx <- which(meta_norm == sample_norm)
  if (length(idx) > 0) {
    return(idx[[1]])
  }
  NA_integer_
}

ensure_metadata <- function(path, samples, info) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  fallback_meta <- data.frame(
    sample = samples,
    condition = rep(c(info$control, info$treatment), length.out = length(samples)),
    stringsAsFactors = FALSE
  )
  fallback_meta$condition[fallback_meta$condition == ""] <- rep(c("group1", "group2"), length.out = length(samples))[fallback_meta$condition == ""]
  if (!file.exists(path)) {
    write.table(fallback_meta, file = path, sep = "\t", quote = FALSE, row.names = FALSE)
    return(invisible(NULL))
  }
  existing <- tryCatch(
    read.delim(path, check.names = FALSE, stringsAsFactors = FALSE, comment.char = "#"),
    error = function(e) NULL
  )
  if (is.null(existing) || ncol(existing) == 0 || nrow(existing) == 0) {
    write.table(fallback_meta, file = path, sep = "\t", quote = FALSE, row.names = FALSE)
    return(invisible(NULL))
  }
  if (!("sample" %in% colnames(existing))) {
    colnames(existing)[1] <- "sample"
  }
  if (!("condition" %in% colnames(existing))) {
    existing$condition <- fallback_meta$condition
  }
  match_idx <- vapply(samples, function(s) match_metadata_idx(s, existing$sample), integer(1))
  if (any(is.na(match_idx))) {
    write.table(fallback_meta, file = path, sep = "\t", quote = FALSE, row.names = FALSE)
    return(invisible(NULL))
  }
  normalized <- existing[match_idx, , drop = FALSE]
  normalized$sample <- samples
  normalized$condition <- as.character(normalized$condition)
  normalized$condition[is.na(normalized$condition) | !nzchar(normalized$condition)] <- fallback_meta$condition[is.na(normalized$condition) | !nzchar(normalized$condition)]
  write.table(normalized, file = path, sep = "\t", quote = FALSE, row.names = FALSE)
}

opts <- parse_args(args)
required <- c("counts", "metadata", "design", "contrast", "outdir")
missing <- required[!vapply(required, function(k) !is.null(opts[[k]]) && nzchar(as.character(opts[[k]])), logical(1))]
if (length(missing) > 0) {
  stop(sprintf("Missing required argument(s): %s", paste(missing, collapse = ", ")))
}

counts_path <- normalizePath(opts$counts, mustWork = TRUE)
metadata_path <- opts$metadata
design_formula <- as.character(opts$design)
contrast_info <- parse_contrast(opts$contrast)
outdir <- opts$outdir
dir.create(outdir, recursive = TRUE, showWarnings = FALSE)

counts <- read.delim(
  counts_path,
  check.names = FALSE,
  stringsAsFactors = FALSE,
  comment.char = "#"
)
if (ncol(counts) < 3) {
  stop("DEXSeq count matrix must include feature identifiers plus sample columns.")
}

id_candidates <- list(
  gene = intersect(c("gene_id", "group_id", "groupID"), colnames(counts)),
  exon = intersect(c("exon_id", "feature_id", "featureID"), colnames(counts))
)
gene_col <- if (length(id_candidates$gene) > 0) id_candidates$gene[[1]] else colnames(counts)[[1]]
exon_col <- if (length(id_candidates$exon) > 0) id_candidates$exon[[1]] else colnames(counts)[[2]]

reserved_cols <- unique(c(gene_col, exon_col))
sample_cols <- setdiff(colnames(counts), reserved_cols)
if (length(sample_cols) < 2) {
  stop("DEXSeq count matrix is missing sample columns.")
}

count_df <- counts[, sample_cols, drop = FALSE]
count_numeric <- suppressWarnings(data.frame(lapply(count_df, as.numeric), check.names = FALSE))
if (any(vapply(count_numeric, function(col) all(is.na(col)), logical(1)))) {
  stop("DEXSeq count matrix contains non-numeric sample columns.")
}
count_mat <- as.matrix(round(count_numeric))
rownames(count_mat) <- paste(counts[[gene_col]], counts[[exon_col]], sep = ":")
colnames(count_mat) <- sample_cols
feature_ids <- make.unique(as.character(counts[[exon_col]]))
group_ids <- as.character(counts[[gene_col]])

ensure_metadata(metadata_path, sample_cols, contrast_info)
metadata <- read.delim(metadata_path, check.names = FALSE, stringsAsFactors = FALSE, comment.char = "#")
if (!("sample" %in% colnames(metadata))) {
  colnames(metadata)[1] <- "sample"
}
if (!("condition" %in% colnames(metadata))) {
  metadata$condition <- rep(c("group1", "group2"), length.out = nrow(metadata))
}

match_idx <- vapply(sample_cols, function(s) match_metadata_idx(s, metadata$sample), integer(1))
if (any(is.na(match_idx))) {
  stop("Metadata does not contain all count-matrix sample names.")
}
metadata <- metadata[match_idx, , drop = FALSE]
condition_values <- as.character(metadata$condition)
if (nzchar(contrast_info$control) && nzchar(contrast_info$treatment)) {
  desired_levels <- unique(c(contrast_info$control, contrast_info$treatment, condition_values))
  metadata$condition <- factor(condition_values, levels = desired_levels)
} else {
  metadata$condition <- factor(condition_values)
}
rownames(metadata) <- sample_cols
metadata$sample <- NULL

if (length(unique(as.character(metadata$condition))) < 2) {
  stop("DEXSeq requires at least two condition levels.")
}

default_design <- "~ sample + exon + condition:exon"
if (!nzchar(design_formula) || !grepl("exon", design_formula, fixed = TRUE)) {
  design_formula <- default_design
}
design <- tryCatch(as.formula(design_formula), error = function(e) as.formula(default_design))
fallback_design <- as.formula("~ exon + condition:exon")

suppressPackageStartupMessages(library(DEXSeq))

build_dataset <- function(design_obj) {
  DEXSeqDataSet(
    countData = count_mat,
    sampleData = metadata,
    design = design_obj,
    featureID = feature_ids,
    groupID = group_ids
  )
}

run_dexseq <- function(design_obj) {
  dxd <- build_dataset(design_obj)
  dxd <- estimateSizeFactors(dxd)
  dxd <- tryCatch(
    estimateDispersions(dxd, quiet = TRUE),
    error = function(e) {
      if (!grepl("standard curve fitting techniques will not work", conditionMessage(e), fixed = TRUE)) {
        stop(e)
      }
      dxd_local <- DESeq2::estimateDispersionsGeneEst(dxd, quiet = TRUE)
      dispersions(dxd_local) <- S4Vectors::mcols(dxd_local)$dispGeneEst
      dxd_local
    }
  )
  dxd <- testForDEU(
    dxd,
    reducedModel = if (grepl("sample", deparse(design_obj), fixed = TRUE)) ~ sample + exon else ~ exon
  )
  fit_var <- if (nzchar(contrast_info$factor_name)) contrast_info$factor_name else "condition"
  estimateExonFoldChanges(dxd, fitExpToVar = fit_var)
}

used_design <- design
dxd <- tryCatch(
  run_dexseq(used_design),
  error = function(e) {
    if (!grepl("model matrix is not full rank", conditionMessage(e), fixed = TRUE)) {
      stop(e)
    }
    used_design <<- fallback_design
    run_dexseq(used_design)
  }
)
result_df <- as.data.frame(DEXSeqResults(dxd))
result_df$gene_id <- rownames(result_df)
for (col_name in colnames(result_df)) {
  column <- result_df[[col_name]]
  if (is.list(column)) {
    result_df[[col_name]] <- vapply(
      column,
      function(value) {
        if (length(value) == 0 || all(is.na(value))) {
          return("")
        }
        paste(as.character(value), collapse = ";")
      },
      character(1)
    )
  }
}

write.table(
  result_df,
  file = file.path(outdir, "dexseq_results.tsv"),
  sep = "\t",
  quote = FALSE,
  row.names = FALSE
)
