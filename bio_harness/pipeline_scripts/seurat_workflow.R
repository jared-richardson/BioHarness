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
required <- c("matrix", "metadata", "output-dir")
missing <- required[!vapply(required, function(k) !is.null(opts[[k]]) && nzchar(as.character(opts[[k]])), logical(1))]
if (length(missing) > 0) {
  stop(sprintf("Missing required argument(s): %s", paste(missing, collapse = ", ")))
}

matrix_path <- normalizePath(opts[["matrix"]], mustWork = TRUE)
metadata_path <- normalizePath(opts[["metadata"]], mustWork = TRUE)
output_dir <- opts[["output-dir"]]
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

counts <- read.delim(
  matrix_path,
  check.names = FALSE,
  stringsAsFactors = FALSE,
  comment.char = "#"
)
if (ncol(counts) < 2) {
  stop("Input matrix is missing cell columns.")
}
gene_ids <- counts[[1]]
count_mat <- as.matrix(data.frame(lapply(counts[, 2:ncol(counts), drop = FALSE], as.numeric), check.names = FALSE))
rownames(count_mat) <- gene_ids
colnames(count_mat) <- colnames(counts)[2:ncol(counts)]

metadata <- read.delim(
  metadata_path,
  check.names = FALSE,
  stringsAsFactors = FALSE,
  comment.char = "#"
)
if (ncol(metadata) == 0 || nrow(metadata) == 0) {
  stop("Metadata table is empty.")
}
if (!("cell" %in% colnames(metadata))) {
  colnames(metadata)[1] <- "cell"
}
match_idx <- match(colnames(count_mat), metadata$cell)
if (any(is.na(match_idx))) {
  stop("Metadata does not contain all matrix cell names.")
}
metadata <- metadata[match_idx, , drop = FALSE]
rownames(metadata) <- metadata$cell

suppressPackageStartupMessages(library(Seurat))

obj <- CreateSeuratObject(counts = count_mat, meta.data = metadata, min.cells = 0, min.features = 0)
obj <- NormalizeData(obj, verbose = FALSE)
variable_features <- min(max(5, floor(nrow(obj) / 2)), nrow(obj))
obj <- FindVariableFeatures(obj, selection.method = "vst", nfeatures = variable_features, verbose = FALSE)
obj <- ScaleData(obj, verbose = FALSE)
npcs <- min(10, max(2, min(nrow(obj) - 1, ncol(obj) - 1)))
obj <- RunPCA(obj, npcs = npcs, verbose = FALSE)
pca_embeddings <- Embeddings(obj, reduction = "pca")
pca_dims <- ncol(pca_embeddings)

cluster_count <- 0
if (ncol(obj) >= 4 && pca_dims >= 2) {
  dims <- seq_len(min(5, pca_dims))
  obj <- FindNeighbors(obj, dims = dims, verbose = FALSE)
  obj <- FindClusters(obj, resolution = 0.2, verbose = FALSE)
  cluster_count <- length(unique(as.character(Idents(obj))))
} else {
  obj$seurat_clusters <- "0"
  Idents(obj) <- "seurat_clusters"
  cluster_count <- 1
}

embedding_df <- data.frame(cell = rownames(pca_embeddings), pca_embeddings, check.names = FALSE)
write.csv(embedding_df, file = file.path(output_dir, "pca_embeddings.csv"), row.names = FALSE)

cell_meta <- obj@meta.data
cell_meta$cell <- rownames(cell_meta)
write.csv(cell_meta, file = file.path(output_dir, "cell_metadata.csv"), row.names = FALSE)

saveRDS(obj, file = file.path(output_dir, "seurat_object.rds"))

summary_json <- sprintf(
  paste0(
    "{\n",
    "  \"cell_count\": %d,\n",
    "  \"gene_count\": %d,\n",
    "  \"variable_feature_count\": %d,\n",
    "  \"cluster_count\": %d\n",
    "}\n"
  ),
  ncol(obj),
  nrow(obj),
  length(VariableFeatures(obj)),
  cluster_count
)
writeLines(summary_json, con = file.path(output_dir, "summary.json"))
