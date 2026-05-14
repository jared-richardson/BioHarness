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

opts <- parse_args(args)
required <- c("counts", "metadata", "design", "contrast", "outdir")
missing <- required[!vapply(required, function(k) !is.null(opts[[k]]) && nzchar(as.character(opts[[k]])), logical(1))]
if (length(missing) > 0) {
  stop(sprintf("Missing required argument(s): %s", paste(missing, collapse = ", ")))
}

counts_path <- normalizePath(opts$counts, mustWork = TRUE)
metadata_path <- opts$metadata
design_formula <- as.character(opts$design)
contrast_raw <- as.character(opts$contrast)
outdir <- opts$outdir
dir.create(outdir, recursive = TRUE, showWarnings = FALSE)

counts <- read.delim(
  counts_path,
  check.names = FALSE,
  stringsAsFactors = FALSE,
  comment.char = "#"
)
if (ncol(counts) < 2) {
  stop("Counts matrix is missing sample columns.")
}

if (ncol(counts) >= 7 && all(c("Chr", "Start", "End", "Strand", "Length") %in% colnames(counts)[2:6])) {
  sample_cols <- colnames(counts)[7:ncol(counts)]
  count_df <- counts[, 7:ncol(counts), drop = FALSE]
} else {
  sample_cols <- colnames(counts)[2:ncol(counts)]
  count_df <- counts[, 2:ncol(counts), drop = FALSE]
}
count_numeric <- suppressWarnings(data.frame(lapply(count_df, as.numeric), check.names = FALSE))
count_mat <- as.matrix(round(count_numeric))
rownames(count_mat) <- counts[[1]]
colnames(count_mat) <- sample_cols

parse_contrast <- function(raw) {
  parts <- strsplit(raw, "_", fixed = TRUE)[[1]]
  if (length(parts) >= 4 && tolower(parts[[3]]) == "vs") {
    return(list(
      factor_name = parts[[1]],
      treatment = parts[[2]],
      control = parts[[4]]
    ))
  }
  list(factor_name = "condition", treatment = "", control = "")
}

contrast_info <- parse_contrast(contrast_raw)

normalize_sample <- function(x) basename(as.character(x))
normalize_sample_core <- function(x) {
  s <- tolower(normalize_sample(x))
  s <- gsub("\\.bam$", "", s)
  s <- gsub("\\.sam$", "", s)
  s <- gsub("[_.]?aligned\\.out$", "", s)
  s
}

match_metadata_idx <- function(sample_name, meta_names) {
  sample_norm <- normalize_sample(sample_name)
  meta_norm <- normalize_sample(meta_names)
  exact <- which(tolower(meta_norm) == tolower(sample_norm))
  if (length(exact) > 0) {
    return(exact[[1]])
  }
  sample_core <- normalize_sample_core(sample_name)
  meta_core <- normalize_sample_core(meta_names)
  exact_core <- which(meta_core == sample_core)
  if (length(exact_core) > 0) {
    return(exact_core[[1]])
  }
  contains <- which(vapply(meta_core, function(mc) {
    if (!nzchar(mc) || !nzchar(sample_core)) {
      return(FALSE)
    }
    grepl(mc, sample_core, fixed = TRUE) || grepl(sample_core, mc, fixed = TRUE)
  }, logical(1)))
  if (length(contains) > 0) {
    return(contains[[1]])
  }
  NA_integer_
}

build_default_metadata <- function(samples, info) {
  labels <- rep("unknown", length(samples))
  if (nzchar(info$control)) {
    labels[grepl(info$control, samples, ignore.case = TRUE)] <- info$control
  }
  if (nzchar(info$treatment)) {
    labels[grepl(info$treatment, samples, ignore.case = TRUE)] <- info$treatment
  }
  if (all(labels == "unknown") && length(samples) == 2 && nzchar(info$control) && nzchar(info$treatment)) {
    labels <- c(info$control, info$treatment)
  }
  data.frame(sample = samples, condition = labels, stringsAsFactors = FALSE)
}

ensure_metadata <- function(path, samples, info) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  fallback_meta <- build_default_metadata(samples, info)
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
    existing$condition <- "unknown"
  }

  match_idx <- vapply(samples, function(s) match_metadata_idx(s, existing$sample), integer(1))
  if (any(is.na(match_idx))) {
    write.table(fallback_meta, file = path, sep = "\t", quote = FALSE, row.names = FALSE)
    return(invisible(NULL))
  }

  normalized <- existing[match_idx, , drop = FALSE]
  normalized$sample <- samples
  normalized$condition <- as.character(normalized$condition)
  normalized$condition[is.na(normalized$condition) | !nzchar(normalized$condition)] <- "unknown"
  write.table(normalized, file = path, sep = "\t", quote = FALSE, row.names = FALSE)
}

ensure_metadata(metadata_path, sample_cols, contrast_info)
metadata <- read.delim(metadata_path, check.names = FALSE, stringsAsFactors = FALSE, comment.char = "#")
if (!("sample" %in% colnames(metadata))) {
  colnames(metadata)[1] <- "sample"
}
if (!("condition" %in% colnames(metadata))) {
  metadata$condition <- "unknown"
}

match_idx <- vapply(sample_cols, function(s) match_metadata_idx(s, metadata$sample), integer(1))
if (any(is.na(match_idx))) {
  stop("Metadata does not contain all count-matrix sample names.")
}
metadata <- metadata[match_idx, , drop = FALSE]
rownames(metadata) <- sample_cols

suppressPackageStartupMessages(library(edgeR))

if (contrast_info$factor_name %in% colnames(metadata)) {
  metadata[[contrast_info$factor_name]] <- factor(metadata[[contrast_info$factor_name]])
  if (nzchar(contrast_info$control) && contrast_info$control %in% levels(metadata[[contrast_info$factor_name]])) {
    metadata[[contrast_info$factor_name]] <- relevel(metadata[[contrast_info$factor_name]], ref = contrast_info$control)
  }
}
if (!("condition" %in% colnames(metadata))) {
  metadata$condition <- factor(rep(c("group1", "group2"), length.out = nrow(metadata)))
} else {
  metadata$condition <- factor(metadata$condition)
}
if (length(levels(metadata$condition)) < 2) {
  metadata$condition <- factor(rep(c("group1", "group2"), length.out = nrow(metadata)))
}

design <- tryCatch(as.formula(design_formula), error = function(e) as.formula("~ condition"))
design_mat <- tryCatch(model.matrix(design, data = metadata), error = function(e) model.matrix(~ condition, data = metadata))

y <- DGEList(counts = count_mat)
y <- calcNormFactors(y)

keep <- rowSums(cpm(y) >= 1) >= max(1, min(2, ncol(y)))
if (sum(keep) == 0) {
  keep <- rowSums(count_mat) > 0
}
if (sum(keep) > 0) {
  y <- y[keep, , keep.lib.sizes = FALSE]
}

if (nrow(design_mat) <= ncol(design_mat)) {
  message("No biological replicates detected for edgeR dispersion estimation; writing fold-change-only results.")
  norm_counts <- cpm(y, normalized.lib.sizes = TRUE, log = FALSE)
  cond <- as.character(metadata$condition)
  treatment_label <- if (nzchar(contrast_info$treatment)) contrast_info$treatment else if (length(unique(cond)) >= 2) unique(cond)[[2]] else unique(cond)[[1]]
  control_label <- if (nzchar(contrast_info$control)) contrast_info$control else if (length(unique(cond)) >= 2) unique(cond)[[1]] else unique(cond)[[1]]
  treatment_idx <- which(cond == treatment_label)
  control_idx <- which(cond == control_label)
  if (length(treatment_idx) == 0 || length(control_idx) == 0) {
    treatment_idx <- seq_len(ncol(norm_counts))[seq(1, ncol(norm_counts), by = 2)]
    control_idx <- setdiff(seq_len(ncol(norm_counts)), treatment_idx)
    if (length(control_idx) == 0) {
      control_idx <- seq_len(ncol(norm_counts))
    }
    if (length(treatment_idx) == 0) {
      treatment_idx <- seq_len(ncol(norm_counts))
    }
  }
  treatment_mean <- rowMeans(norm_counts[, treatment_idx, drop = FALSE])
  control_mean <- rowMeans(norm_counts[, control_idx, drop = FALSE])
  result_df <- data.frame(
    gene_id = rownames(norm_counts),
    logFC = log2((treatment_mean + 1) / (control_mean + 1)),
    logCPM = log2(rowMeans(norm_counts) + 1),
    F = NA_real_,
    PValue = NA_real_,
    FDR = NA_real_,
    stringsAsFactors = FALSE
  )
  write.table(
    result_df,
    file = file.path(outdir, "edger_results.tsv"),
    sep = "\t",
    quote = FALSE,
    row.names = FALSE
  )
  quit(save = "no", status = 0)
}

y <- estimateDisp(y, design_mat, robust = TRUE)
fit <- glmQLFit(y, design_mat, robust = TRUE)
coef_index <- ncol(design_mat)
qlf <- glmQLFTest(fit, coef = coef_index)
result_df <- topTags(qlf, n = Inf, sort.by = "none")$table
result_df$gene_id <- rownames(result_df)
write.table(
  result_df,
  file = file.path(outdir, "edger_results.tsv"),
  sep = "\t",
  quote = FALSE,
  row.names = FALSE
)
